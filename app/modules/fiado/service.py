"""Regras do fiado (caderneta): clientes, compras, pagamentos, estornos, extrato, LGPD.

Toda operação que mexe em dinheiro do caixa grava o lançamento do cliente E o
movimento do caixa na MESMA transação (tudo ou nada).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import auditoria
from app.core.datas import agora_utc, hoje_local
from app.core.dinheiro import LIMITE_CONFIRMACAO_CENTAVOS, formatar_reais
from app.core.erros import ErroNegocio, SemPermissao
from app.modules.auth.models import Usuario
from app.modules.caixa import repository as caixa_repo
from app.modules.caixa import service as caixa
from app.modules.caixa.models import Origem, SessaoCaixa, TipoMovimento
from app.modules.caixa.schemas import Parte
from app.modules.caixa.service import PreviaParte
from app.modules.fiado import repository as repo
from app.modules.fiado.calculo import (
    ItemFiado,
    compra_mais_antiga_em_aberto,
    faixa_atraso,
    link_whatsapp,
    mensagem_extrato,
    normalizar_busca,
)
from app.modules.fiado.models import SINAL_FIADO, Cliente, FiadoLancamento, TipoFiado
from app.modules.fiado.schemas import DadosCliente, Excedente, NovaCompra, NovoPagamento

# ---------------------------------------------------------------- cadastro


def buscar_clientes(db: Session, termo: str, incluir_inativos: bool = False) -> list[Cliente]:
    return repo.buscar_clientes(db, normalizar_busca(termo), incluir_inativos)


def obter_cliente(db: Session, cliente_id: int) -> Cliente:
    cliente = repo.buscar_cliente(db, cliente_id)
    if cliente is None:
        raise ErroNegocio("Cliente não encontrado.")
    return cliente


def criar_cliente(
    db: Session,
    usuario: Usuario,
    dados: DadosCliente,
    ip: str | None,
    campos_extras: bool = False,
) -> Cliente:
    if not dados.consentimento:
        raise ErroNegocio("Marque que o cliente autorizou o cadastro (LGPD).")
    agora = agora_utc()
    cliente = Cliente(
        nome=dados.nome,
        apelido=dados.apelido,
        telefone=dados.telefone,
        observacao=dados.observacao,
        # Só o administrador define limite de crédito.
        limite_centavos=dados.limite_centavos if usuario.eh_admin else None,
        dia_pagamento=dados.dia_pagamento,
        cpf=dados.cpf if campos_extras else None,
        endereco=dados.endereco if campos_extras else None,
        saldo_centavos=0,
        ativo=True,
        busca=normalizar_busca(dados.nome, dados.apelido, dados.telefone),
        consentimento_em=agora,
        criado_em=agora,
    )
    db.add(cliente)
    db.flush()
    auditoria.registrar(
        db,
        acao="cliente_criado",
        entidade="cliente",
        entidade_id=cliente.id,
        usuario_id=usuario.id,
        depois=cliente.resumo(),
        ip=ip,
    )
    db.commit()
    return cliente


def editar_cliente(
    db: Session,
    usuario: Usuario,
    cliente_id: int,
    dados: DadosCliente,
    ativo: bool,
    ip: str | None,
    campos_extras: bool = False,
) -> Cliente:
    cliente = obter_cliente(db, cliente_id)
    if cliente.anonimizado:
        raise ErroNegocio("Cliente anonimizado não pode ser alterado.")
    antes = cliente.resumo()
    if usuario.eh_admin:
        if not ativo and cliente.saldo_centavos != 0:
            raise ErroNegocio("Cliente com saldo (dívida ou crédito) não pode ser desativado.")
        cliente.limite_centavos = dados.limite_centavos
        cliente.ativo = ativo
    cliente.nome = dados.nome
    cliente.apelido = dados.apelido
    cliente.telefone = dados.telefone
    cliente.observacao = dados.observacao
    cliente.dia_pagamento = dados.dia_pagamento
    if campos_extras:  # desligado: mantém o que já existia (não apaga sem querer)
        cliente.cpf = dados.cpf
        cliente.endereco = dados.endereco
    cliente.busca = normalizar_busca(dados.nome, dados.apelido, dados.telefone)
    auditoria.registrar(
        db,
        acao="cliente_editado",
        entidade="cliente",
        entidade_id=cliente.id,
        usuario_id=usuario.id,
        antes=antes,
        depois=cliente.resumo(),
        ip=ip,
    )
    db.commit()
    return cliente


# ---------------------------------------------------------------- auxiliares de gravação


def _exigir_admin(usuario: Usuario, acao: str) -> None:
    if not usuario.eh_admin:
        raise SemPermissao(f"Só o administrador pode {acao}.")


def _cliente_ativo(db: Session, cliente_id: int) -> Cliente:
    cliente = obter_cliente(db, cliente_id)
    if not cliente.ativo:
        raise ErroNegocio("Este cliente está desativado. Peça ao administrador para reativar.")
    return cliente


def _exigir_valor_confirmado(valor: int, confirmado: bool) -> None:
    if valor > LIMITE_CONFIRMACAO_CENTAVOS and not confirmado:
        raise ErroNegocio(
            f"Valor acima de {formatar_reais(LIMITE_CONFIRMACAO_CENTAVOS)}. "
            "Confira e marque a confirmação de valor alto."
        )


def _validar_formas(db: Session, partes: list[Parte]) -> list[PreviaParte]:
    ids = [p.forma_id for p in partes]
    if len(set(ids)) != len(ids):
        raise ErroNegocio("A mesma forma de pagamento foi informada duas vezes.")
    previas = []
    for parte in partes:
        forma = caixa_repo.buscar_forma(db, parte.forma_id)
        if forma is None or not forma.ativo:
            raise ErroNegocio("Forma de pagamento inválida. Atualize a tela.")
        previas.append(PreviaParte(forma.nome, parte.valor_centavos, forma.afeta_gaveta))
    return previas


def _novo_lancamento(
    cliente: Cliente,
    usuario: Usuario,
    tipo: TipoFiado,
    valor: int,
    chave: str,
    *,
    descricao: str | None = None,
    data_referencia: date | None = None,
    sessao: SessaoCaixa | None = None,
    movimento_caixa_id: int | None = None,
    autorizado_por: int | None = None,
) -> FiadoLancamento:
    return FiadoLancamento(
        cliente_id=cliente.id,
        tipo=tipo,
        sinal=SINAL_FIADO[tipo],
        valor_centavos=valor,
        descricao=descricao,
        data_referencia=data_referencia or hoje_local(),
        sessao_caixa_id=sessao.id if sessao else None,
        movimento_caixa_id=movimento_caixa_id,
        usuario_id=usuario.id,
        autorizado_por=autorizado_por,
        chave_idempotencia=chave,
        criado_em=agora_utc(),
    )


def _gravar(
    db: Session,
    cliente: Cliente,
    lancamento: FiadoLancamento,
    usuario: Usuario,
    ip: str | None,
) -> tuple[FiadoLancamento, bool]:
    """Grava o lançamento, atualiza o saldo em cache, audita e confirma (commit)."""
    try:
        db.add(lancamento)
        cliente.saldo_centavos += lancamento.valor_com_sinal
        db.flush()
    except IntegrityError:
        db.rollback()
        existente = repo.lancamento_por_chave(db, lancamento.chave_idempotencia)
        if existente is None:
            raise
        return existente, True
    auditoria.registrar(
        db,
        acao=f"fiado_{lancamento.tipo}",
        entidade="fiado_lancamento",
        entidade_id=lancamento.id,
        usuario_id=usuario.id,
        depois={
            "cliente_id": cliente.id,
            "tipo": lancamento.tipo,
            "valor_centavos": lancamento.valor_centavos,
            "descricao": lancamento.descricao,
            "movimento_caixa_id": lancamento.movimento_caixa_id,
            "estorno_de_id": lancamento.estorno_de_id,
            "autorizado_por": lancamento.autorizado_por,
            "saldo_depois": cliente.saldo_centavos,
        },
        ip=ip,
    )
    db.commit()
    return lancamento, False


# ---------------------------------------------------------------- compra


@dataclass(frozen=True)
class PreviaCompra:
    cliente: Cliente
    total_centavos: int
    partes_pagas: list[PreviaParte]
    pago_agora_centavos: int
    valor_fiado_centavos: int
    saldo_antes_centavos: int
    saldo_depois_centavos: int
    excede_limite: bool
    valor_alto: bool
    data_referencia: date
    descricao: str | None


def revisar_compra(db: Session, dados: NovaCompra) -> PreviaCompra:
    caixa.exigir_sessao_para_lancar(db)
    cliente = _cliente_ativo(db, dados.cliente_id)
    if dados.data_referencia > hoje_local():
        raise ErroNegocio("A data da compra não pode ser no futuro.")
    if dados.pago_agora_centavos > dados.valor_total_centavos:
        raise ErroNegocio("O valor pago agora é maior que o total da compra.")
    if dados.pago_agora_centavos == dados.valor_total_centavos:
        raise ErroNegocio("Foi tudo pago na hora: lance como Entrada no caixa, não como fiado.")
    partes = _validar_formas(db, dados.partes_pagas)
    depois = cliente.saldo_centavos + dados.valor_fiado_centavos
    return PreviaCompra(
        cliente=cliente,
        total_centavos=dados.valor_total_centavos,
        partes_pagas=partes,
        pago_agora_centavos=dados.pago_agora_centavos,
        valor_fiado_centavos=dados.valor_fiado_centavos,
        saldo_antes_centavos=cliente.saldo_centavos,
        saldo_depois_centavos=depois,
        excede_limite=cliente.limite_centavos is not None and depois > cliente.limite_centavos,
        valor_alto=dados.valor_total_centavos > LIMITE_CONFIRMACAO_CENTAVOS,
        data_referencia=dados.data_referencia,
        descricao=dados.descricao,
    )


def anotar_compra(
    db: Session,
    usuario: Usuario,
    dados: NovaCompra,
    autorizador: Usuario | None,
    confirmar_limite: bool,
    ip: str | None,
) -> tuple[FiadoLancamento, bool]:
    """Anota a compra fiado (não entra no caixa). Com "pagou agora", a parte paga
    entra no caixa como Entrada, na mesma transação."""
    repetido = repo.lancamento_por_chave(db, str(dados.chave))
    if repetido is not None:
        return repetido, True

    previa = revisar_compra(db, dados)
    _exigir_valor_confirmado(previa.total_centavos, dados.confirmar_valor_alto)
    autorizado_por = _autorizacao_limite(previa, usuario, autorizador, confirmar_limite)
    sessao = caixa.exigir_sessao_para_lancar(db)
    cliente = previa.cliente

    movimento_id = None
    if dados.partes_pagas:
        movimento = caixa.montar_movimento(
            sessao,
            usuario,
            TipoMovimento.ENTRADA,
            [(p.forma_id, p.valor_centavos) for p in dados.partes_pagas],
            chave=f"{dados.chave}:caixa",
            descricao="Parte paga na hora de compra fiado",
            origem=Origem.FIADO,
            origem_id=cliente.id,
        )
        caixa.inserir_movimento(db, movimento, usuario, "lancamento_caixa", ip)
        movimento_id = movimento.id

    lancamento = _novo_lancamento(
        cliente,
        usuario,
        TipoFiado.COMPRA,
        previa.valor_fiado_centavos,
        str(dados.chave),
        descricao=dados.descricao,
        data_referencia=dados.data_referencia,
        sessao=sessao,
        movimento_caixa_id=movimento_id,
        autorizado_por=autorizado_por,
    )
    return _gravar(db, cliente, lancamento, usuario, ip)


def _autorizacao_limite(
    previa: PreviaCompra, usuario: Usuario, autorizador: Usuario | None, confirmado: bool
) -> int | None:
    """Passar do limite exige o administrador: ele mesmo confirmando, ou a senha dele."""
    if not previa.excede_limite:
        return None
    if usuario.eh_admin:
        if not confirmado:
            raise ErroNegocio("Esta compra passa do limite do cliente. Marque a autorização.")
        return usuario.id
    if autorizador is None or not autorizador.eh_admin:
        raise SemPermissao(
            "Esta compra passa do limite do cliente: precisa da senha do administrador."
        )
    return autorizador.id


# ---------------------------------------------------------------- pagamento


@dataclass(frozen=True)
class PreviaPagamento:
    cliente: Cliente
    total_informado_centavos: int
    partes: list[PreviaParte]  # o que efetivamente fica registrado no caixa
    divida_centavos: int
    excesso_centavos: int
    troco_centavos: int
    valor_registrado_centavos: int
    saldo_depois_centavos: int
    pode_dar_troco: bool
    precisa_escolher: bool
    valor_alto: bool


def _partes_registradas(db: Session, dados: NovoPagamento, troco: int) -> list[tuple[int, int]]:
    """Desconta o troco da parte em dinheiro (o troco sai da gaveta)."""
    partes = []
    for parte in dados.partes:
        valor = parte.valor_centavos
        forma = caixa_repo.buscar_forma(db, parte.forma_id)
        if troco and forma is not None and forma.afeta_gaveta:
            valor -= troco
            troco = 0
        if valor > 0:
            partes.append((parte.forma_id, valor))
    return partes


def revisar_pagamento(db: Session, dados: NovoPagamento) -> PreviaPagamento:
    caixa.exigir_sessao_para_lancar(db)
    cliente = obter_cliente(db, dados.cliente_id)  # cliente desativado também pode pagar
    _validar_formas(db, dados.partes)

    total = dados.total_centavos
    divida = max(cliente.saldo_centavos, 0)
    excesso = max(total - divida, 0)
    em_dinheiro = sum(
        p.valor_centavos
        for p in dados.partes
        if (f := caixa_repo.buscar_forma(db, p.forma_id)) is not None and f.afeta_gaveta
    )
    pode_troco = excesso > 0 and divida > 0 and em_dinheiro >= excesso
    precisa_escolher = excesso > 0 and dados.excedente is None

    troco = 0
    if excesso and dados.excedente == Excedente.TROCO:
        if not pode_troco:
            raise ErroNegocio(
                "Só dá para devolver troco quando o valor a mais foi pago em dinheiro. "
                "Escolha deixar como crédito do cliente."
            )
        troco = excesso
    registrado = total - troco
    partes = [
        PreviaParte(f.nome, valor, f.afeta_gaveta)
        for forma_id, valor in _partes_registradas(db, dados, troco)
        if (f := caixa_repo.buscar_forma(db, forma_id)) is not None
    ]
    return PreviaPagamento(
        cliente=cliente,
        total_informado_centavos=total,
        partes=partes,
        divida_centavos=divida,
        excesso_centavos=excesso,
        troco_centavos=troco,
        valor_registrado_centavos=registrado,
        saldo_depois_centavos=cliente.saldo_centavos - registrado,
        pode_dar_troco=pode_troco,
        precisa_escolher=precisa_escolher,
        valor_alto=total > LIMITE_CONFIRMACAO_CENTAVOS,
    )


def receber_pagamento(
    db: Session, usuario: Usuario, dados: NovoPagamento, ip: str | None
) -> tuple[FiadoLancamento, bool]:
    """Pagamento total ou parcial: baixa no cliente + Recebimento de fiado no caixa."""
    repetido = repo.lancamento_por_chave(db, str(dados.chave))
    if repetido is not None:
        return repetido, True

    previa = revisar_pagamento(db, dados)
    if previa.precisa_escolher:
        raise ErroNegocio(
            "O pagamento é maior que a dívida: escolha devolver troco ou deixar crédito."
        )
    _exigir_valor_confirmado(previa.total_informado_centavos, dados.confirmar_valor_alto)
    sessao = caixa.exigir_sessao_para_lancar(db)
    cliente = previa.cliente

    movimento = caixa.montar_movimento(
        sessao,
        usuario,
        TipoMovimento.RECEBIMENTO_FIADO,
        _partes_registradas(db, dados, previa.troco_centavos),
        chave=f"{dados.chave}:caixa",
        descricao="Recebimento de fiado",
        origem=Origem.FIADO,
        origem_id=cliente.id,
    )
    caixa.inserir_movimento(db, movimento, usuario, "lancamento_caixa", ip)
    descricao = "Pagamento" + (
        " (excedente como crédito)" if previa.excesso_centavos and not previa.troco_centavos else ""
    )
    lancamento = _novo_lancamento(
        cliente,
        usuario,
        TipoFiado.PAGAMENTO,
        previa.valor_registrado_centavos,
        str(dados.chave),
        descricao=descricao,
        sessao=sessao,
        movimento_caixa_id=movimento.id,
    )
    return _gravar(db, cliente, lancamento, usuario, ip)


# -------------------------------------------------- abatimento, saldo inicial, devolução


def _motivo(texto: str | None, rotulo: str = "o motivo") -> str:
    motivo = " ".join((texto or "").split())
    if len(motivo) < 3:
        raise ErroNegocio(f"Escreva {rotulo}.")
    return motivo[:200]


def abater(
    db: Session,
    usuario: Usuario,
    cliente_id: int,
    valor: int,
    motivo: str,
    chave: UUID,
    ip: str | None,
) -> tuple[FiadoLancamento, bool]:
    """Perdão/desconto/devolução de mercadoria. Só admin. Não passa pelo caixa."""
    repetido = repo.lancamento_por_chave(db, str(chave))
    if repetido is not None:
        return repetido, True
    _exigir_admin(usuario, "abater dívida")
    texto = _motivo(motivo)
    cliente = obter_cliente(db, cliente_id)
    if valor <= 0:
        raise ErroNegocio("O valor precisa ser maior que zero.")
    if valor > cliente.saldo_centavos:
        divida = formatar_reais(cliente.saldo_centavos)
        raise ErroNegocio(f"O abatimento não pode ser maior que a dívida ({divida}).")
    lancamento = _novo_lancamento(
        cliente,
        usuario,
        TipoFiado.ABATIMENTO,
        valor,
        str(chave),
        descricao=texto,
        sessao=caixa_repo.sessao_aberta(db),
        autorizado_por=usuario.id,
    )
    return _gravar(db, cliente, lancamento, usuario, ip)


def lancar_saldo_inicial(
    db: Session,
    usuario: Usuario,
    cliente_id: int,
    valor: int,
    data_referencia: date,
    descricao: str | None,
    chave: UUID,
    ip: str | None,
) -> tuple[FiadoLancamento, bool]:
    """Dívida trazida do caderno de papel. Só admin; não precisa de caixa aberto."""
    repetido = repo.lancamento_por_chave(db, str(chave))
    if repetido is not None:
        return repetido, True
    _exigir_admin(usuario, "lançar saldo do caderno")
    cliente = _cliente_ativo(db, cliente_id)
    if valor <= 0:
        raise ErroNegocio("O valor precisa ser maior que zero.")
    if data_referencia > hoje_local():
        raise ErroNegocio("A data não pode ser no futuro.")
    texto = " ".join((descricao or "").split())[:200] or "Saldo trazido do caderno"
    lancamento = _novo_lancamento(
        cliente,
        usuario,
        TipoFiado.SALDO_INICIAL,
        valor,
        str(chave),
        descricao=texto,
        data_referencia=data_referencia,
        autorizado_por=usuario.id,
    )
    return _gravar(db, cliente, lancamento, usuario, ip)


def devolver_credito(
    db: Session,
    usuario: Usuario,
    cliente_id: int,
    valor: int,
    chave: UUID,
    ip: str | None,
) -> tuple[FiadoLancamento, bool]:
    """Cliente com crédito recebe o dinheiro de volta: saída no caixa + lançamento no fiado."""
    repetido = repo.lancamento_por_chave(db, str(chave))
    if repetido is not None:
        return repetido, True
    sessao = caixa.exigir_sessao_para_lancar(db)
    cliente = obter_cliente(db, cliente_id)
    credito = -cliente.saldo_centavos
    if credito <= 0:
        raise ErroNegocio("Este cliente não tem crédito para devolver.")
    if not 0 < valor <= credito:
        raise ErroNegocio(f"O valor deve ser entre R$ 0,01 e {formatar_reais(credito)}.")
    dinheiro = caixa_repo.forma_dinheiro(db)
    categoria = caixa_repo.categoria_devolucao_fiado(db)
    if dinheiro is None or categoria is None:
        raise ErroNegocio("Configuração do caixa incompleta (dinheiro/categoria). Avise o suporte.")
    partes = [(dinheiro.id, valor)]
    caixa.exigir_dinheiro_na_gaveta(db, sessao, partes)

    movimento = caixa.montar_movimento(
        sessao,
        usuario,
        TipoMovimento.SAIDA,
        partes,
        chave=f"{chave}:caixa",
        categoria_id=categoria.id,
        descricao="Devolução de crédito a cliente do fiado",
        origem=Origem.FIADO,
        origem_id=cliente.id,
    )
    caixa.inserir_movimento(db, movimento, usuario, "lancamento_caixa", ip)
    lancamento = _novo_lancamento(
        cliente,
        usuario,
        TipoFiado.DEVOLUCAO_CREDITO,
        valor,
        str(chave),
        descricao="Crédito devolvido em dinheiro",
        sessao=sessao,
        movimento_caixa_id=movimento.id,
    )
    return _gravar(db, cliente, lancamento, usuario, ip)


# ---------------------------------------------------------------- estorno


def obter_lancamento(db: Session, lancamento_id: int) -> FiadoLancamento:
    lancamento = repo.buscar_lancamento(db, lancamento_id)
    if lancamento is None:
        raise ErroNegocio("Lançamento não encontrado.")
    return lancamento


def validar_estornavel(db: Session, original: FiadoLancamento) -> None:
    if original.tipo == TipoFiado.ESTORNO:
        raise ErroNegocio("Não é possível estornar um estorno.")
    if repo.estorno_de(db, original.id) is not None:
        raise ErroNegocio("Este lançamento já foi estornado.")


def estornar(
    db: Session,
    usuario: Usuario,
    lancamento_id: int,
    motivo: str,
    chave: UUID,
    autorizador: Usuario | None,
    ip: str | None,
) -> tuple[FiadoLancamento, bool]:
    """Lança o inverso exato. Se o original passou pelo caixa, estorna o caixa junto."""
    repetido = repo.lancamento_por_chave(db, str(chave))
    if repetido is not None:
        return repetido, True
    if not usuario.eh_admin and (autorizador is None or not autorizador.eh_admin):
        raise SemPermissao("Estorno precisa da senha do administrador.")
    texto = _motivo(motivo, "o motivo do estorno")
    original = obter_lancamento(db, lancamento_id)
    validar_estornavel(db, original)
    cliente = obter_cliente(db, original.cliente_id)
    aprovador = autorizador or usuario

    sessao = caixa_repo.sessao_aberta(db)
    movimento_estorno_id = None
    if original.movimento_caixa_id is not None:
        sessao = caixa.exigir_sessao_para_lancar(db)
        mov_original = caixa.obter_movimento(db, original.movimento_caixa_id)
        if caixa_repo.estorno_de(db, mov_original.id) is not None:
            raise ErroNegocio("O lançamento do caixa ligado a este já foi estornado.")
        mov_estorno = caixa.montar_estorno(
            db, sessao, usuario, mov_original, texto, f"{chave}:caixa", aprovador
        )
        caixa.inserir_movimento(db, mov_estorno, usuario, "estorno_caixa", ip)
        movimento_estorno_id = mov_estorno.id

    lancamento = FiadoLancamento(
        cliente_id=cliente.id,
        tipo=TipoFiado.ESTORNO,
        sinal=-original.sinal,
        valor_centavos=original.valor_centavos,
        descricao=texto,
        data_referencia=hoje_local(),
        sessao_caixa_id=sessao.id if sessao else None,
        movimento_caixa_id=movimento_estorno_id,
        usuario_id=usuario.id,
        autorizado_por=aprovador.id,
        estorno_de_id=original.id,
        chave_idempotencia=str(chave),
        criado_em=agora_utc(),
    )
    return _gravar(db, cliente, lancamento, usuario, ip)


# ---------------------------------------------------------------- extrato e painel


def _itens(lancamentos: list[FiadoLancamento]) -> list[ItemFiado]:
    return [
        ItemFiado(
            id=lanc.id,
            sinal=lanc.sinal,
            valor_centavos=lanc.valor_centavos,
            data=lanc.data_referencia,
            eh_estorno=lanc.tipo == TipoFiado.ESTORNO,
            estorno_de_id=lanc.estorno_de_id,
        )
        for lanc in lancamentos
    ]


@dataclass(frozen=True)
class LinhaExtrato:
    lancamento: FiadoLancamento
    saldo_apos_centavos: int


@dataclass(frozen=True)
class Extrato:
    cliente: Cliente
    linhas: list[LinhaExtrato]
    saldo_centavos: int
    compra_mais_antiga: date | None
    dias_em_aberto: int | None
    estornados: set[int]
    link_whatsapp: str | None


def extrato(db: Session, cliente_id: int, nome_loja: str) -> Extrato:
    cliente = obter_cliente(db, cliente_id)
    lancamentos = repo.lancamentos_do_cliente(db, cliente.id)
    linhas = []
    acumulado = 0
    for lanc in lancamentos:
        acumulado += lanc.valor_com_sinal
        linhas.append(LinhaExtrato(lanc, acumulado))
    mais_antiga = compra_mais_antiga_em_aberto(_itens(lancamentos))
    dias = (hoje_local() - mais_antiga).days if mais_antiga else None
    mensagem = mensagem_extrato(cliente.tratamento, nome_loja, cliente.saldo_centavos, mais_antiga)
    return Extrato(
        cliente=cliente,
        linhas=linhas,
        saldo_centavos=cliente.saldo_centavos,
        compra_mais_antiga=mais_antiga,
        dias_em_aberto=dias,
        estornados={lanc.estorno_de_id for lanc in lancamentos if lanc.estorno_de_id},
        link_whatsapp=None if cliente.anonimizado else link_whatsapp(cliente.telefone, mensagem),
    )


@dataclass(frozen=True)
class LinhaPainel:
    cliente: Cliente
    saldo_centavos: int
    desde: date | None
    dias: int | None
    faixa: str


@dataclass
class Painel:
    total_a_receber_centavos: int = 0
    clientes_devendo: int = 0
    total_creditos_centavos: int = 0
    por_faixa: dict[str, int] = field(default_factory=dict)
    linhas: list[LinhaPainel] = field(default_factory=list)


def painel(db: Session, ordem: str = "valor") -> Painel:
    clientes = repo.clientes_com_saldo(db)
    por_cliente: dict[int, list[FiadoLancamento]] = defaultdict(list)
    for lanc in repo.lancamentos_de_clientes(db, [c.id for c in clientes]):
        por_cliente[lanc.cliente_id].append(lanc)

    resultado = Painel()
    faixas: dict[str, int] = defaultdict(int)
    hoje = hoje_local()
    for cliente in clientes:
        saldo = cliente.saldo_centavos
        if saldo < 0:
            resultado.total_creditos_centavos += -saldo
            continue
        desde = compra_mais_antiga_em_aberto(_itens(por_cliente[cliente.id]))
        dias = (hoje - desde).days if desde else None
        faixa = faixa_atraso(dias)
        resultado.total_a_receber_centavos += saldo
        resultado.clientes_devendo += 1
        faixas[faixa] += saldo
        resultado.linhas.append(LinhaPainel(cliente, saldo, desde, dias, faixa))

    if ordem == "atraso":
        resultado.linhas.sort(key=lambda linha: (-(linha.dias or 0), -linha.saldo_centavos))
    else:
        resultado.linhas.sort(key=lambda linha: -linha.saldo_centavos)
    resultado.por_faixa = dict(faixas)
    return resultado


# ---------------------------------------------------------------- LGPD


def anonimizar(db: Session, usuario: Usuario, cliente_id: int, ip: str | None) -> Cliente:
    """Troca nome/telefone por 'Cliente anonimizado #id'. Valores do histórico ficam."""
    _exigir_admin(usuario, "anonimizar clientes")
    cliente = obter_cliente(db, cliente_id)
    if cliente.anonimizado:
        raise ErroNegocio("Este cliente já foi anonimizado.")
    if cliente.saldo_centavos != 0:
        raise ErroNegocio("Só é possível anonimizar cliente com saldo zerado.")
    cliente.nome = f"Cliente anonimizado #{cliente.id}"
    cliente.apelido = None
    cliente.telefone = None
    cliente.observacao = None
    cliente.cpf = None
    cliente.endereco = None
    cliente.dia_pagamento = None
    cliente.ativo = False
    cliente.busca = normalizar_busca(cliente.nome)
    cliente.anonimizado_em = agora_utc()
    # Auditoria SEM os dados pessoais apagados (senão a anonimização seria inútil).
    auditoria.registrar(
        db,
        acao="cliente_anonimizado",
        entidade="cliente",
        entidade_id=cliente.id,
        usuario_id=usuario.id,
        ip=ip,
    )
    db.commit()
    return cliente


def exportar_dados(
    db: Session, usuario: Usuario, cliente_id: int, ip: str | None
) -> dict[str, Any]:
    """Direito de acesso do titular (LGPD art. 18): cadastro + histórico."""
    _exigir_admin(usuario, "exportar dados de clientes")
    cliente = obter_cliente(db, cliente_id)
    lancamentos = repo.lancamentos_do_cliente(db, cliente.id)
    auditoria.registrar(
        db,
        acao="cliente_dados_exportados",
        entidade="cliente",
        entidade_id=cliente.id,
        usuario_id=usuario.id,
        ip=ip,
    )
    db.commit()
    return {
        "cliente": {
            **cliente.dados_pessoais(),
            "cadastrado_em": cliente.criado_em.isoformat(),
            "consentimento_em": cliente.consentimento_em.isoformat(),
            "saldo_centavos": cliente.saldo_centavos,
        },
        "lancamentos": [
            {
                "data": lanc.data_referencia.isoformat(),
                "tipo": lanc.rotulo,
                "valor_centavos": lanc.valor_com_sinal,
                "descricao": lanc.descricao,
            }
            for lanc in lancamentos
        ],
    }


# ---------------------------------------------------------------- verificação


def verificar_saldos(db: Session) -> list[str]:
    """Recalcula o saldo de cada cliente a partir dos lançamentos e aponta divergências."""
    problemas = []
    for cliente in repo.todos_clientes(db):
        calculado = repo.saldo_calculado(db, cliente.id)
        if calculado != cliente.saldo_centavos:
            problemas.append(
                f"Cliente #{cliente.id}: saldo gravado {formatar_reais(cliente.saldo_centavos)}"
                f" difere do recalculado {formatar_reais(calculado)}."
            )
    return problemas
