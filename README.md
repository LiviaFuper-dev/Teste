# Discord Support Bot – Caveira Sistemas

Este projeto contém um bot de suporte técnico para Discord, desenvolvido em Python com `discord.py`, responsável por automatizar o atendimento inicial de problemas relacionados a sistemas internos da empresa.

O bot cria tópicos de atendimento (threads), conduz o usuário por um fluxo de diagnóstico automatizado e, quando necessário, encaminha o chamado para a equipe responsável.

Além disso, o bot integra com N8N para registro e automação de fluxos de suporte.

---

## Principais Funcionalidades

### 📌 Abertura de chamados automatizada

Usuários podem abrir chamados diretamente no Discord. Ao iniciar um atendimento, o bot:

- cria uma thread privada de suporte
- solicita informações iniciais sobre o problema
- registra os dados do atendimento

### 🧠 Diagnóstico automatizado

O bot conduz o usuário por um fluxo interativo de diagnóstico, utilizando:

- botões
- formulários
- perguntas sequenciais

Dependendo das respostas do usuário, o bot pode:

- sugerir ações de solução
- coletar mais informações
- encaminhar o chamado para a equipe técnica

### ⚠️ Identificação automática de erros

O bot possui uma base de soluções (`solutions.json`) contendo códigos de erro e suas respectivas resoluções.

Quando o usuário informa um erro:

1. O bot tenta identificar automaticamente o código
2. Procura na base de soluções
3. Caso encontre:
   - envia a solução diretamente no privado do usuário
   - registra o evento
   - encerra o atendimento automático

Caso não encontre solução, o chamado é encaminhado para a equipe de suporte humano.

### 👥 Encaminhamento para equipes responsáveis

Dependendo do tipo de problema, o bot pode acionar automaticamente equipes como:

- ChatGuru
- Whom
- Suporte técnico geral
- ClickUp Support

Isso é feito através da menção automática de cargos específicos no Discord.

### 📝 Registro de diagnóstico

Durante o atendimento, o bot registra cada etapa do diagnóstico:

- tipo de problema
- ações tentadas
- código de erro informado
- solução encontrada ou não

Essas informações são organizadas em um payload estruturado, que posteriormente pode ser enviado para integrações externas.

### 🔗 Integração com N8N

O bot envia dados de atendimento para um webhook do N8N, permitindo:

- automação de processos
- criação de registros de suporte
- integração com outros sistemas internos

---

## Estrutura do Projeto

```
.
├── caveira-sistemas.py
├── caveira-contato.py
├── caveira-suporte.py
├── solutions.json
├── .gitignore
```

### `caveira-sistemas.py`

Arquivo principal do bot. Responsável por:

- conexão com o Discord
- criação de threads de suporte
- fluxo de diagnóstico automatizado
- interação com usuários via botões e formulários
- comunicação com o webhook do N8N

### `solutions.json`

Base de conhecimento utilizada pelo bot. Contém códigos de erro e possíveis soluções utilizadas para resposta automática aos usuários.

---

## Variáveis de Ambiente

O projeto utiliza um arquivo `.env` para armazenar credenciais sensíveis. Exemplo:

```env
DISCORD_TOKEN=seu_token_do_bot
N8N_WEBHOOK_URL=https://webhook-n8n
```

---

## Tecnologias Utilizadas

- Python
- discord.py
- asyncio
- requests
- dotenv
- JSON
- N8N (automação de fluxos)

---

## Objetivo do Projeto

O bot foi desenvolvido para:

- reduzir o volume de atendimentos manuais
- automatizar diagnósticos simples
- organizar chamados técnicos no Discord
- integrar suporte com sistemas de automação