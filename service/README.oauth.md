# Conexão do MCP com o ChatGPT

O servidor mantém o acesso direto por `Authorization: Bearer <chave>` e oferece
OAuth authorization-code com PKCE S256 para conectar ao ChatGPT. A chave do
proprietário é informada somente na página HTTPS do próprio serviço. O ChatGPT
recebe tokens temporários, nunca a chave do Render.

## Render

- Mantenha `WATERMARKS_SERVER_API_KEY` com uma chave aleatória e privada.
- O endereço público usa `WATERMARKS_PUBLIC_URL`, depois `RENDER_EXTERNAL_URL`.
  Sem essas variáveis, usa `https://watermarks-remover-cjct.onrender.com`.
- O Dockerfile atual já copia `scripts/mcp_oauth.py`. Não são necessárias
  dependências adicionais ou mudanças no `.dockerignore`.
- Sem a chave do proprietário, o MCP recusa acesso. Não remova a variável
  para tentar corrigir uma falha na conexão.

## Configuração do plugin

Em cada servidor de `mcp.json`, configure:

```json
{
  "type": "streamable-http",
  "url": "https://watermarks-remover-cjct.onrender.com/mcp",
  "extensions": {
    "com.openai": {
      "auth": {
        "type": "oauth",
        "client": {"mode": "dcr"},
        "baseScopes": ["files:manage"],
        "defaultScopes": ["files:manage"]
      }
    }
  }
}
```

Depois da implantação, abra a conexão do plugin e autorize no Render. Confira
o domínio antes de colar a chave. Teste `capabilities` antes de anexar um arquivo.
O teste final precisa ser feito no ChatGPT com a conta do proprietário.
Após aceitar a chave, a página confirma a autorização e volta ao callback
validado do ChatGPT por navegação HTML. O link “Voltar ao ChatGPT” permite
concluir a conexão se o aplicativo impedir a navegação automática. Não há
redirecionamento externo do POST, que navegadores podem bloquear pela CSP.

## Limites deste serviço privado

Esta implementação atende a um único proprietário e um único processo do
servidor. O cadastro DCR aceita somente os callbacks HTTPS do ChatGPT. Não é
um provedor de identidade para oferecer contas a terceiros.

Tokens de acesso expiram em uma hora. Tokens de renovação são rotacionados a
cada uso e expiram em sete dias. Códigos de autorização e tokens de
renovação ficam em memória, com limite de entradas. Depois de um reinício ou
uma nova implantação, a conexão pode pedir autorização novamente. Os clientes
DCR continuam válidos enquanto a chave e o endereço público forem mantidos.
Trocar a chave invalida clientes e tokens existentes.

O formulário contém os parâmetros OAuth assinados, com validade de dez minutos,
e exige a chave do proprietário em cada envio. Não usa sessão de login ou
credenciais automáticas e não depende de cookies. Origens externas explícitas
são recusadas. Aplicativos que não enviam Origin ou enviam `null` precisam
apresentar o mesmo formulário assinado e a chave correta. O formulário sobrevive
a um reinício do serviço se a chave e o endereço público forem mantidos.
Há limite global de dez tentativas de chave incorreta por minuto. Os logs do
MCP omitem query strings e corpos. Para vários usuários ou réplicas, use um
provedor de identidade e armazenamento de sessão compartilhado.

## Validação

```sh
python3 -m unittest discover -s tests -p test_mcp_oauth.py -v
```

Os testes HTTP cobrem descoberta OAuth, login e consentimento, PKCE, vínculo
ao recurso e ao cliente, uso único dos códigos, renovação e rejeição de replay,
proteção do formulário, chave incorreta, ausência de chave, adulteração dos
tokens, manutenção do acesso direto e inspeção real de um texto pelo MCP.
