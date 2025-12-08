#!/bin/bash

# Runner em nível de conta (serve todos os repos)
./config.sh \
    --url "https://github.com/${GITHUB_OWNER}" \
    --token "${GITHUB_TOKEN}" \
    --name "${RUNNER_NAME:-railway-runner}" \
    --work "_work" \
    --labels "railway,self-hosted,linux,x64" \
    --unattended \
    --replace

./run.sh
```

Commit e push desses 2 arquivos.

---

## 2. Gerar Token no GitHub (Nível de Conta)

### Para Conta Pessoal:

**Acesse:** `https://github.com/settings/tokens`

1. Clique **"Generate new token (classic)"**
2. Nome: `Railway Runner`
3. Expiration: **No expiration** (ou 1 ano)
4. Selecione scopes:
   - ✅ `repo` (Full control of private repositories)
   - ✅ `workflow` (Update GitHub Action workflows)
5. Clique **"Generate token"**
6. **COPIE o token** (aparece 1 vez só): `ghp_xxxxxxxxxxxx`

### Para Organização:

**Acesse:** `https://github.com/organizations/{sua-org}/settings/actions/runners/new`

1. Clique **"New self-hosted runner"**
2. **Copie o token** que aparece
3. **OU** use token com scope `admin:org`

---

## 3. Deploy na Railway

### 3.1. Criar Projeto

1. Acesse: **https://railway.app/**
2. Clique **"New Project"**
3. Selecione **"Deploy from GitHub repo"**
4. Escolha **`git-runner`**

### 3.2. Configurar Variáveis

Na aba **"Variables"**, adicione:
```
GITHUB_OWNER = seu-usuario
GITHUB_TOKEN = ghp_seu_token_aqui
RUNNER_NAME = railway-runner-prod
```

**Importante:** 
- Substitua `seu-usuario` pelo seu username do GitHub
- Se for organização, use o nome da org
- Deixe `GITHUB_REPO` VAZIO (não crie essa variável)

### 3.3. Deploy

1. Railway detecta o Dockerfile automaticamente
2. Deploy inicia sozinho
3. Aguarde status **"SUCCESS"** (1-2 minutos)

---

## 4. Verificar Runner Ativo

### GitHub - Nível de Conta:

**Acesse:** `https://github.com/settings/actions/runners`

Você verá:
```
🟢 railway-runner-prod
   Status: Idle
   Labels: self-hosted, Linux, X64, railway
