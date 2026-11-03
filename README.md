# Twitch Chat Hub

Um programa para Windows que fica na bandeja do sistema (ao lado do relógio) e abre **só os chats** dos canais da Twitch que você escolher, sem abrir as transmissões. Suporta até 4 contas ao mesmo tempo, cada uma com o seu próprio login.

> Projeto não oficial, sem relação com a Twitch. "Twitch" é marca registrada da Twitch Interactive, Inc.

## Baixar e usar

1. Abra a página de [Releases](https://github.com/JotaEme7/Twtich-Chat-hub/releases/latest) e baixe o `TwitchChatHub.exe`.
2. Dê dois cliques nele. O ícone aparece na bandeja, ao lado do relógio (se não aparecer, olhe na setinha `^`).
3. Clique no ícone para abrir o painel.

Você não precisa ter Python instalado para usar o `.exe`.

### Aviso do Windows na primeira vez

Como o arquivo não tem assinatura digital, o Windows (SmartScreen) ou o antivírus pode mostrar um aviso. Clique em **Mais informações** e depois em **Executar assim mesmo**. O código está todo neste repositório, para você conferir.

## O que ele faz

- **Painel escuro** que abre ao clicar no ícone da bandeja. Fecha só no X, então dá para deixá-lo aberto ao lado.
- **Canais:** adicione pelo nome, `@nome` ou link da Twitch.
- **Até 4 contas**, cada uma com seu perfil de navegador e login próprio. Você entra na Twitch uma vez em cada conta e o login fica salvo.
- **Canais por conta:** escolha quais canais cada conta mostra.
- **Duas formas de abrir:** todos os chats lado a lado numa janela só, ou uma janela por canal.
- **Cor e nome por conta**, para saber qual é qual de relance (a cor aparece também no topo das colunas da janela única).
- **Status e contador:** cada conta mostra quantas janelas tem abertas, há quanto tempo, e o tempo total acumulado com janelas abertas.
- **Lembra a posição e o tamanho** de cada janela.
- **Abrir tudo ao iniciar** e **iniciar com o Windows**, se você quiser.
- **Fechar todas as janelas** com um clique. Ao sair do programa, as janelas que ele abriu também fecham.
- **Atualização dentro do programa** (veja abaixo).

## Requisitos

- Windows 10 ou 11.
- Um navegador. O programa procura **Chrome, Edge ou Brave** e abre os chats em janelas limpas, sem abas. Outro navegador baseado em Chromium pode ser escolhido em **Configurações > Navegador**, sem garantia de funcionar. Sem nenhum deles, os chats abrem no navegador padrão, e aí as contas 2 a 4 não ficam separadas.

## Atualizações

O programa consulta esta página de releases ao iniciar e a cada poucas horas. Quando há versão nova, aparece o botão **Atualizar agora** no painel. Ele baixa o novo `.exe`, confere o código de verificação (SHA-256) publicado na release e, se bater, troca o arquivo e abre de novo. Se algo falhar, o `.exe` antigo continua funcionando e a página da release é aberta para você baixar à mão.

Suas contas, canais e configurações ficam em `%APPDATA%\TwitchChatHub` e não são apagados ao atualizar.

## Problemas comuns

- **Os chats aparecem mas não consigo escrever.** Entre na Twitch dentro da própria janela do chat. Alguns navegadores bloqueiam o login dentro de chats embutidos. Nesse caso use **Janelas separadas**, onde cada chat é uma página própria da Twitch.
- **Uma janela abriu fora da tela.** Em **Configurações > Janelas**, use **Esquecer posições salvas**.
- **O painel não abre ou algum botão não responde.** Veja o arquivo `%APPDATA%\TwitchChatHub\error.log` e abra uma issue com o conteúdo.
- **O tempo total não bate com as horas de outro site ou jogo.** O contador soma só o tempo em que as janelas ficaram abertas pelo hub. Não é uma medida do que outro serviço conta.

## Privacidade

O programa não coleta nem envia dados. Ele só faz uma consulta pública ao GitHub para saber se há versão nova. Tudo o mais fica no seu computador, na pasta `%APPDATA%\TwitchChatHub`.

## Uso responsável

Use as suas contas respeitando os [Termos de Serviço da Twitch](https://www.twitch.tv/p/legal/terms-of-service/). Escrever a mesma coisa em várias contas no mesmo chat pode cair em filtros de spam ou resultar em punição.

## Para quem quer rodar pelo código

```
pip install -r requirements.txt
python twitch_chat_hub.pyw
```

Para gerar o `.exe`:

```
pip install pyinstaller
python -m PyInstaller --clean --noconsole --onefile --add-data "twitch.ico;." --name TwitchChatHub twitch_chat_hub.pyw
```

(O `--add-data` e o `twitch.ico` são opcionais: sem o arquivo, o programa usa um ícone desenhado por ele mesmo.)

### Publicar uma versão nova

1. Aumente o número em `VERSION` no código.
2. Gere o `.exe` e o código de verificação:
   ```
   powershell -command "(Get-FileHash 'dist\TwitchChatHub.exe' -Algorithm SHA256).Hash.ToLower() | Out-File -Encoding ascii 'dist\TwitchChatHub.exe.sha256'"
   ```
3. Crie uma release com a tag igual à versão (por exemplo `v1.4`) e anexe `TwitchChatHub.exe` e `TwitchChatHub.exe.sha256`. O nome do `.exe` precisa ser exatamente esse, porque é o que a atualização automática procura.
