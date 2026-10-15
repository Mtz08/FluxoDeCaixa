"""Regras do caixa: abrir, lançar, estornar, fechar (cego) e resumir.

Cada função pública que grava é uma operação completa: termina com commit()
ou levanta exceção — nunca grava pela metade.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import auditoria
from app.core.datas import agora_utc, formatar_data, hoje_local, para_local
from app.core.dinheiro import LIMITE_CONFIRMACAO_CENTAVOS, formatar_reais
from app.core.erros import ErroNegocio, SemPermissao
from app.modules.auth.models import Usuario
from app.modules.caixa import repository as repo
from app.modules.caixa.calculo import ItemResumo, ResumoCaixa, calcular_resumo
from app.modules.caixa.models import (
    CATEGORIA_DEVOLUCAO_FIADO,
    SINAL_DO_TIPO,
    FormaPagamento,
    MovimentoCaixa,
    MovimentoPagamento,
    Origem,
    SessaoCaixa,
    StatusSessao,
    TipoMovimento,
)
from app.modules.caixa.schemas import TIPOS_MANUAIS, NovoLancamento

TOLERANCIA_RELOGIO = timedelta(minutes=5)


# ---------------------------------------------------------------- consultas de estado


def sessao_aberta(db: Session) -> SessaoCaixa | None:
    return repo.sessao_aberta(db)


def sessao_de_dia_anterior(sessao: SessaoCaixa | None) -> bool:
    """Caixa esquecido aberto de um dia para o outro."""
    return sessao is not None and para_local(sessao.aberta_em).date() < hoje_local()


def alerta_relogio(db: Session) -> str | None:
    """Avisa se o relógio do PC está antes do último registro gravado."""
    ultimo = repo.ultimo_registro_em(db)
    if ultimo and agora_utc() + TOLERANCIA_RELOGIO < ultimo:
        return (
            "A data/hora do computador parece errada: está antes do último lançamento "
            f"({formatar_data(para_local(ultimo).date())}). Corrija o relógio do Windows."
        )
    return None


def exigir_sessao_para_lancar(db: Session) -> SessaoCaixa:
    sessao = repo.sessao_aberta(db)
    if sessao is None:
        raise ErroNegocio("O caixa está fechado. Abra o caixa antes de lançar.")
    if sessao_de_dia_anterior(sessao):
        dia = formatar_data(para_local(sessao.aberta_em).date())
        raise ErroNegocio(
            f"O caixa do dia {dia} ainda está aberto. Feche esse caixa antes de continuar."
        )
    return sessao


# ---------------------------------------------------------------- abertura


def abrir_caixa(
    db: Session, usuario: Usuario, fundo_centavos: int, chave: UUID, ip: str | None
) -> SessaoCaixa:
    repetida = repo.sessao_por_chave(db, str(chave))
    if repetida is not None:  # duplo clique: já foi aberto por este envio
        return repetida
    if fundo_centavos < 0:
        raise ErroNegocio("O fundo de troco não pode ser negativo.")
    atual = repo.sessao_aberta(db)
    if atual is not None:
        raise ErroNegocio("Já existe um caixa aberto. Feche-o antes de abrir outro.")

    sessao = SessaoCaixa(
        status=StatusSessao.ABERTA,
        aberta_por=usuario.id,
        aberta_em=agora_utc(),
        fundo_troco_centavos=fundo_centavos,
        chave_idempotencia=str(chave),
    )
    db.add(sessao)
    try:
        db.flush()
    except IntegrityError as erro:  # outro terminal abriu no mesmo instante
        db.rollback()
        raise ErroNegocio("Já existe um caixa aberto. Atualize a tela.") from erro
    auditoria.registrar(
        db,
        acao="caixa_aberto",
        entidade="sessao_caixa",
        entidade_id=sessao.id,
        usuario_id=usuario.id,
        depois={"fundo_troco_centavos": fundo_centavos},
        ip=ip,
    )
    db.commit()
    return sessao


# ---------------------------------------------------------------- lançamentos


@dataclass(frozen=True)
class PreviaParte:
    forma: str
    valor_centavos: int
    afeta_gaveta: bool


@dataclass(frozen=True)
class PreviaLancamento:
    """O que será gravado — mostrado na tela de confirmação."""

    tipo: TipoMovimento
    partes: list[PreviaParte]
    total_centavos: int
    categoria: str | None
    descricao: str | None
    valor_alto: bool


def revisar_lancamento(db: Session, dados: NovoLancamento) -> PreviaLancamento:
    """Valida tudo sem gravar. Levanta ErroNegocio com mensagem para a tela."""
    sessao = exigir_sessao_para_lancar(db)
    if dados.tipo not in TIPOS_MANUAIS:
        raise ErroNegocio("Tipo de lançamento inválido nesta tela.")

    formas = _validar_partes(db, dados)
    categoria = _validar_categoria(db, dados)
    if dados.tipo == TipoMovimento.SANGRIA and not dados.descricao:
        raise ErroNegocio("Informe o motivo da sangria (ex.: depósito no banco).")
    so_dinheiro = len(formas) == 1 and formas[0].afeta_gaveta
    if dados.tipo in (TipoMovimento.SANGRIA, TipoMovimento.SUPRIMENTO) and not so_dinheiro:
        raise ErroNegocio("Sangria e suprimento são sempre em dinheiro.")
    _exigir_dinheiro_na_gaveta(db, sessao, dados)

    total = dados.total_centavos
    return PreviaLancamento(
        tipo=dados.tipo,
        partes=[
            PreviaParte(f.nome, p.valor_centavos, f.afeta_gaveta)
            for f, p in zip(formas, dados.partes, strict=True)
        ],
        total_centavos=total,
        categoria=categoria,
        descricao=dados.descricao,
        valor_alto=total > LIMITE_CONFIRMACAO_CENTAVOS,
    )


def lancar(
    db: Session, usuario: Usuario, dados: NovoLancamento, ip: str | None
) -> tuple[MovimentoCaixa, bool]:
    """Grava o lançamento. Devolve (movimento, repetido); repetido=True = envio duplicado."""
    repetido = repo.movimento_por_chave(db, str(dados.chave))
    if repetido is not None:
        return repetido, True

    previa = revisar_lancamento(db, dados)
    if previa.valor_alto and not dados.confirmar_valor_alto:
        raise ErroNegocio(
            f"Valor acima de {formatar_reais(LIMITE_CONFIRMACAO_CENTAVOS)}. "
            "Confira e marque a confirmação de valor alto."
        )
    sessao = exigir_sessao_para_lancar(db)
    movimento = montar_movimento(
        sessao,
        usuario,
        dados.tipo,
        [(p.forma_id, p.valor_centavos) for p in dados.partes],
        chave=str(dados.chave),
        categoria_id=dados.categoria_id if dados.tipo == TipoMovimento.SAIDA else None,
        descricao=dados.descricao,
    )
    return _gravar_movimento(db, movimento, usuario, "lancamento_caixa", ip)


def _validar_partes(db: Session, dados: NovoLancamento) -> list[FormaPagamento]:
    ids = [p.forma_id for p in dados.partes]
    if len(set(ids)) != len(ids):
        raise ErroNegocio("A mesma forma de pagamento foi informada duas vezes.")
    formas: list[FormaPagamento] = []
    for parte in dados.partes:
        forma = repo.buscar_forma(db, parte.forma_id)
        if forma is None or not forma.ativo:
            raise ErroNegocio("Forma de pagamento inválida. Atualize a tela.")
        formas.append(forma)
    return formas


def _validar_categoria(db: Session, dados: NovoLancamento) -> str | None:
    if dados.tipo != TipoMovimento.SAIDA:
        return None
    if dados.categoria_id is None:
        raise ErroNegocio("Escolha a categoria da despesa.")
    categoria = repo.buscar_categoria(db, dados.categoria_id)
    if categoria is None or not categoria.ativo or categoria.nome == CATEGORIA_DEVOLUCAO_FIADO:
        raise ErroNegocio("Categoria inválida. Atualize a tela.")
    return categoria.nome


def _exigir_dinheiro_na_gaveta(db: Session, sessao: SessaoCaixa, dados: NovoLancamento) -> None:
    if SINAL_DO_TIPO[dados.tipo] < 0:
        exigir_dinheiro_na_gaveta(
            db, sessao, [(p.forma_id, p.valor_centavos) for p in dados.partes]
        )


def exigir_dinheiro_na_gaveta(
    db: Session,
    sessao: SessaoCaixa,
    partes: list[tuple[int, int]],
    mensagem: str = (
        "Esse valor é maior do que o dinheiro que deveria estar na gaveta. "
        "Confira o valor ou fale com o administrador."
    ),
) -> None:
    """Não deixa tirar da gaveta mais dinheiro do que deveria haver nela.

    `partes` = [(forma_pagamento_id, valor_centavos)] do dinheiro que vai SAIR.
    """
    em_dinheiro = sum(
        valor
        for forma_id, valor in partes
        if (forma := repo.buscar_forma(db, forma_id)) is not None and forma.afeta_gaveta
    )
    if em_dinheiro and em_dinheiro > resumo_da_sessao(db, sessao).esperado_gaveta_centavos:
        raise ErroNegocio(mensagem)


def montar_movimento(
    sessao: SessaoCaixa,
    usuario: Usuario,
    tipo: TipoMovimento,
    partes: list[tuple[int, int]],
    *,
    chave: str,
    categoria_id: int | None = None,
    descricao: str | None = None,
    origem: Origem = Origem.MANUAL,
    origem_id: int | None = None,
) -> MovimentoCaixa:
    """Monta (sem gravar) um movimento. Usado pelo caixa e por outros módulos (fiado, PDV)."""
    movimento = MovimentoCaixa(
        sessao_id=sessao.id,
        tipo=tipo,
        sinal=SINAL_DO_TIPO[tipo],
        valor_centavos=sum(valor for _, valor in partes),
        categoria_id=categoria_id,
        descricao=descricao,
        usuario_id=usuario.id,
        considera_gaveta=True,
        origem=origem,
        origem_id=origem_id,
        chave_idempotencia=chave,
        criado_em=agora_utc(),
    )
    movimento.partes = [
        MovimentoPagamento(forma_pagamento_id=forma_id, valor_centavos=valor)
        for forma_id, valor in partes
    ]
    return movimento


def inserir_movimento(
    db: Session, movimento: MovimentoCaixa, usuario: Usuario, acao: str, ip: str | None
) -> None:
    """Insere e audita SEM commit: quem chama controla a transação."""
    db.add(movimento)
    db.flush()
    auditoria.registrar(
        db,
        acao=acao,
        entidade="movimento_caixa",
        entidade_id=movimento.id,
        usuario_id=usuario.id,
        depois=_dados_auditoria(movimento),
        ip=ip,
    )


def _gravar_movimento(
    db: Session, movimento: MovimentoCaixa, usuario: Usuario, acao: str, ip: str | None
) -> tuple[MovimentoCaixa, bool]:
    try:
        inserir_movimento(db, movimento, usuario, acao, ip)
    except IntegrityError:
        # Envio duplicado chegou ao mesmo tempo: o outro já gravou.
        db.rollback()
        existente = repo.movimento_por_chave(db, movimento.chave_idempotencia)
        if existente is None:
            raise
        return existente, True
    db.commit()
    return movimento, False


def _dados_auditoria(movimento: MovimentoCaixa) -> dict[str, object]:
    return {
        "sessao_id": movimento.sessao_id,
        "tipo": movimento.tipo,
        "sinal": movimento.sinal,
        "valor_centavos": movimento.valor_centavos,
        "categoria_id": movimento.categoria_id,
        "descricao": movimento.descricao,
        "estorno_de_id": movimento.estorno_de_id,
        "autorizado_por": movimento.autorizado_por,
        "considera_gaveta": movimento.considera_gaveta,
        "partes": [(p.forma_pagamento_id, p.valor_centavos) for p in movimento.partes],
    }


# ---------------------------------------------------------------- estorno


def obter_movimento(db: Session, movimento_id: int) -> MovimentoCaixa:
    movimento = repo.buscar_movimento(db, movimento_id)
    if movimento is None:
        raise ErroNegocio("Lançamento não encontrado.")
    return movimento


def validar_estornavel(db: Session, original: MovimentoCaixa) -> None:
    if original.tipo == TipoMovimento.ESTORNO:
        raise ErroNegocio("Não é possível estornar um estorno.")
    if original.origem != Origem.MANUAL:
        raise ErroNegocio("Este lançamento veio do Fiado: estorne pela ficha do cliente.")
    if repo.estorno_de(db, original.id) is not None:
        raise ErroNegocio("Este lançamento já foi estornado.")


def estornar(
    db: Session,
    usuario: Usuario,
    movimento_id: int,
    motivo: str,
    chave: UUID,
    autorizador: Usuario | None,
    ip: str | None,
) -> tuple[MovimentoCaixa, bool]:
    """Lança o inverso exato do original. Operador precisa de autorização do admin.

    Original de caixa já fechado: o estorno entra no caixa atual, mas NÃO mexe
    na gaveta de hoje (o dinheiro daquele dia já foi contado no fechamento dele).
    """
    repetido = repo.movimento_por_chave(db, str(chave))
    if repetido is not None:
        return repetido, True

    if not usuario.eh_admin and (autorizador is None or not autorizador.eh_admin):
        raise SemPermissao("Estorno precisa da senha do administrador.")
    motivo = " ".join(motivo.split())
    if len(motivo) < 3:
        raise ErroNegocio("Escreva o motivo do estorno.")
    original = obter_movimento(db, movimento_id)
    validar_estornavel(db, original)
    sessao = exigir_sessao_para_lancar(db)
    movimento = montar_estorno(db, sessao, usuario, original, motivo, str(chave), autorizador)
    return _gravar_movimento(db, movimento, usuario, "estorno_caixa", ip)


def montar_estorno(
    db: Session,
    sessao: SessaoCaixa,
    usuario: Usuario,
    original: MovimentoCaixa,
    motivo: str,
    chave: str,
    autorizador: Usuario | None,
) -> MovimentoCaixa:
    """Monta (sem gravar) o inverso exato do original, conferindo o dinheiro da gaveta."""
    mesma_sessao = original.sessao_id == sessao.id
    movimento = MovimentoCaixa(
        sessao_id=sessao.id,
        tipo=TipoMovimento.ESTORNO,
        sinal=-original.sinal,
        valor_centavos=original.valor_centavos,
        categoria_id=original.categoria_id,
        descricao=motivo[:200],
        usuario_id=usuario.id,
        autorizado_por=(autorizador or usuario).id,
        estorno_de_id=original.id,
        considera_gaveta=mesma_sessao and original.considera_gaveta,
        origem=original.origem,
        origem_id=original.origem_id,
        chave_idempotencia=chave,
        criado_em=agora_utc(),
    )
    movimento.partes = [
        MovimentoPagamento(forma_pagamento_id=p.forma_pagamento_id, valor_centavos=p.valor_centavos)
        for p in original.partes
    ]
    if movimento.considera_gaveta and movimento.sinal < 0:
        # Estornar uma entrada em dinheiro também tira dinheiro da gaveta.
        exigir_dinheiro_na_gaveta(
            db,
            sessao,
            [(p.forma_pagamento_id, p.valor_centavos) for p in movimento.partes],
            "Não há dinheiro suficiente na gaveta para devolver esse valor.",
        )
    return movimento


# ---------------------------------------------------------------- fechamento cego


@dataclass(frozen=True)
class Conferencia:
    sessao_id: int
    contado_centavos: int
    diferenca_centavos: int  # positivo = sobra; negativo = falta


def calcular_conferencia(db: Session, sessao_id: int, contado_centavos: int) -> Conferencia:
    """Compara o contado com o esperado, sem gravar nada."""
    sessao = _sessao_para_fechar(db, sessao_id)
    if contado_centavos < 0:
        raise ErroNegocio("O valor contado não pode ser negativo.")
    esperado = resumo_da_sessao(db, sessao).esperado_gaveta_centavos
    return Conferencia(sessao.id, contado_centavos, contado_centavos - esperado)


def conferir_fechamento(
    db: Session, usuario: Usuario, sessao_id: int, contado_centavos: int, ip: str | None
) -> Conferencia:
    """Passo 1 do fechamento: mostra sobra/falta (nunca o esperado).

    Toda contagem fica na auditoria: recontar depois de ver a diferença deixa rastro.
    """
    conferencia = calcular_conferencia(db, sessao_id, contado_centavos)
    auditoria.registrar(
        db,
        acao="caixa_contagem",
        entidade="sessao_caixa",
        entidade_id=sessao_id,
        usuario_id=usuario.id,
        depois={"contado_centavos": contado_centavos},
        ip=ip,
    )
    db.commit()
    return conferencia


def fechar_caixa(
    db: Session,
    usuario: Usuario,
    sessao_id: int,
    contado_centavos: int,
    justificativa: str | None,
    ip: str | None,
) -> SessaoCaixa:
    sessao = _sessao_para_fechar(db, sessao_id)
    ultima_contagem = auditoria.ultimo_registro(
        db, acao="caixa_contagem", entidade="sessao_caixa", entidade_id=sessao.id
    )
    # O fechamento grava exatamente a última contagem conferida (fechamento cego).
    if ultima_contagem is None or ultima_contagem.get("contado_centavos") != contado_centavos:
        raise ErroNegocio("Faça a conferência da contagem antes de fechar o caixa.")
    esperado = resumo_da_sessao(db, sessao).esperado_gaveta_centavos
    diferenca = contado_centavos - esperado
    justificativa = " ".join((justificativa or "").split()) or None
    if diferenca != 0 and (justificativa is None or len(justificativa) < 5):
        raise ErroNegocio("Houve diferença no caixa: escreva uma justificativa.")

    sessao.status = StatusSessao.FECHADA
    sessao.fechada_por = usuario.id
    sessao.usuario_fechamento = usuario  # mantém a relação em memória coerente com o id
    sessao.fechada_em = agora_utc()
    sessao.valor_contado_centavos = contado_centavos
    sessao.valor_esperado_centavos = esperado
    sessao.diferenca_centavos = diferenca
    sessao.justificativa = justificativa[:500] if justificativa else None
    auditoria.registrar(
        db,
        acao="caixa_fechado",
        entidade="sessao_caixa",
        entidade_id=sessao.id,
        usuario_id=usuario.id,
        depois={"contado": contado_centavos, "esperado": esperado, "diferenca": diferenca},
        ip=ip,
    )
    db.commit()
    # Etapa 5: disparar backup automático aqui.
    return sessao


def _sessao_para_fechar(db: Session, sessao_id: int) -> SessaoCaixa:
    sessao = repo.buscar_sessao(db, sessao_id)
    if sessao is None:
        raise ErroNegocio("Caixa não encontrado.")
    if not sessao.aberta:
        raise ErroNegocio("Este caixa já foi fechado.")
    return sessao


# ---------------------------------------------------------------- resumo


@dataclass(frozen=True)
class ResumoSessao:
    sessao: SessaoCaixa
    resumo: ResumoCaixa
    movimentos: list[MovimentoCaixa]
    estornados: set[int]


def resumo_da_sessao(db: Session, sessao: SessaoCaixa) -> ResumoCaixa:
    movimentos = repo.movimentos_da_sessao(db, sessao.id)
    resumo = calcular_resumo(sessao.fundo_troco_centavos, itens_resumo(movimentos))
    resumo.estornos_qtd = sum(1 for m in movimentos if m.tipo == TipoMovimento.ESTORNO)
    return resumo


def itens_resumo(movimentos: list[MovimentoCaixa]) -> list[ItemResumo]:
    """Converte movimentos em itens do cálculo (uma linha por parte de pagamento)."""
    return [
        ItemResumo(
            tipo_efetivo=m.tipo_efetivo,
            eh_estorno=m.tipo == TipoMovimento.ESTORNO,
            sinal=m.sinal,
            valor_centavos=p.valor_centavos,
            forma=p.forma.nome,
            forma_ordem=p.forma.ordem,
            afeta_gaveta=p.forma.afeta_gaveta,
            considera_gaveta=m.considera_gaveta,
        )
        for m in movimentos
        for p in m.partes
    ]


def pode_ver_sessao(usuario: Usuario, sessao: SessaoCaixa) -> bool:
    """Admin vê tudo. Operador: o caixa aberto, ou o que ele mesmo fechou hoje (imprimir)."""
    if usuario.eh_admin or sessao.aberta:
        return True
    return (
        sessao.fechada_por == usuario.id
        and sessao.fechada_em is not None
        and para_local(sessao.fechada_em).date() == hoje_local()
    )


def obter_resumo(db: Session, usuario: Usuario, sessao_id: int) -> ResumoSessao:
    sessao = repo.buscar_sessao(db, sessao_id)
    if sessao is None:
        raise ErroNegocio("Caixa não encontrado.")
    if not pode_ver_sessao(usuario, sessao):
        raise SemPermissao("Só o administrador pode ver caixas de outros dias.")
    movimentos = repo.movimentos_da_sessao(db, sessao.id)
    resumo = calcular_resumo(sessao.fundo_troco_centavos, itens_resumo(movimentos))
    resumo.estornos_qtd = sum(1 for m in movimentos if m.tipo == TipoMovimento.ESTORNO)
    estornados = repo.ids_estornados(db, [m.id for m in movimentos])
    return ResumoSessao(sessao, resumo, movimentos, estornados)


# ---------------------------------------------------------------- verificação


def verificar_consistencia(db: Session) -> list[str]:
    """Recalcula tudo a partir dos lançamentos e aponta divergências."""
    problemas = [
        f"Lançamento #{mid}: a soma das formas de pagamento não bate com o valor."
        for mid in repo.movimentos_com_partes_divergentes(db)
    ]
    for sessao in repo.sessoes_fechadas(db):
        recalculado = resumo_da_sessao(db, sessao).esperado_gaveta_centavos
        gravado = sessao.valor_esperado_centavos
        if recalculado != gravado:
            problemas.append(
                f"Caixa #{sessao.id}: esperado gravado {formatar_reais(gravado)}"
                f" difere do recalculado {formatar_reais(recalculado)}."
            )
    return problemas
