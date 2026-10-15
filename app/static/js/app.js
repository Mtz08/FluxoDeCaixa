// Comportamentos comuns a todas as telas. Sem dependências além do HTMX.
// O servidor valida tudo de novo: o JavaScript só deixa o balcão mais rápido.
"use strict";

// ------------------------------------------------------------ CSRF no HTMX
document.addEventListener("htmx:configRequest", (evento) => {
  const token = document.body.dataset.csrf;
  if (token) evento.detail.headers["X-CSRF-Token"] = token;
});

// ------------------------------------------------------------ duplo clique
// Desabilita o botão após o primeiro envio (o servidor também usa chave de idempotência).
document.addEventListener("submit", (evento) => {
  const botoes = evento.target.querySelectorAll("button[type=submit]");
  botoes.forEach((b) => {
    setTimeout(() => { b.disabled = true; }, 0);
  });
});
window.addEventListener("pageshow", () => {
  document.querySelectorAll("button[type=submit]").forEach((b) => { b.disabled = false; });
});

// ------------------------------------------------------------ dinheiro
// Máscara de caixa registradora: digitar 1 2 5 0 mostra 12,50.
function centavosDoTexto(texto) {
  const digitos = String(texto || "").replace(/\D/g, "");
  return digitos ? parseInt(digitos, 10) : 0;
}

function formatarCampo(centavos) {
  const reais = Math.floor(centavos / 100).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return reais + "," + String(centavos % 100).padStart(2, "0");
}

function formatarReais(centavos) {
  const sinal = centavos < 0 ? "-" : "";
  return "R$ " + sinal + formatarCampo(Math.abs(centavos));
}

function aplicarMascara(campo) {
  const centavos = centavosDoTexto(campo.value);
  campo.value = centavos ? formatarCampo(Math.min(centavos, 99999999999)) : "";
}

document.addEventListener("input", (evento) => {
  const campo = evento.target;
  if (campo.matches && campo.matches("[data-moeda]")) {
    aplicarMascara(campo);
    const form = campo.closest("form");
    if (form) atualizarTotais(form);
  }
});

// Total ao vivo e troco na tela de entrada.
function atualizarTotais(form) {
  const saidaTotal = form.querySelector("[data-total]");
  if (!saidaTotal) return;
  let total = 0;
  let dinheiro = 0;
  form.querySelectorAll("[data-parte]").forEach((campo) => {
    const v = centavosDoTexto(campo.value);
    total += v;
    if (campo.hasAttribute("data-dinheiro")) dinheiro += v;
  });
  saidaTotal.textContent = formatarReais(total);

  const saidaTroco = form.querySelector("[data-troco]");
  const recebido = form.querySelector("[data-recebido]");
  if (saidaTroco && recebido) {
    const valorRecebido = centavosDoTexto(recebido.value);
    const troco = valorRecebido - dinheiro;
    if (!valorRecebido) {
      saidaTroco.textContent = formatarReais(0);
    } else if (troco < 0) {
      saidaTroco.textContent = "faltam " + formatarReais(-troco);
    } else {
      saidaTroco.textContent = formatarReais(troco);
    }
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-moeda]").forEach((campo) => {
    if (campo.value) aplicarMascara(campo);
  });
  document.querySelectorAll("form").forEach(atualizarTotais);
});

// ------------------------------------------------------------ atalhos de teclado
// F2 entrada, F3 saída, F4 fiado, F6 sangria, F7 suprimento, F8 fechar, F9 receber fiado;
// Esc volta ao início. (F5 fica livre: é o "recarregar" do navegador.)
document.addEventListener("keydown", (evento) => {
  if (evento.key === "Escape" && document.querySelector("[data-lancamento]")) {
    window.location.href = "/";
    return;
  }
  const alvo = document.querySelector(`[data-atalho="${evento.key}"]`);
  if (alvo) {
    evento.preventDefault();
    alvo.click();
  }
});

// ------------------------------------------------------------ impressão
document.addEventListener("click", (evento) => {
  if (evento.target.closest && evento.target.closest("[data-imprimir]")) window.print();
});
