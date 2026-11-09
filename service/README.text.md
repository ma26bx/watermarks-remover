# Recursos leves de texto no MCP

Sem bibliotecas adicionais, modelos, GPU ou serviços pagos.

## Ferramentas

- `analyze_text(text)`: aceita texto colado e retorna contagens, indicadores
  descritivos de legibilidade, palavras e sequências de duas/três palavras
  repetidas, estrutura Markdown, Unicode com posições, estilometria e Gumbel.
- `normalize_text(text, options)`: retorna uma cópia com limpeza Unicode,
  relatório das mudanças e espaços normalizados. Não reescreve frases, não
  usa LLM e não altera o original. As opções booleanas `nfkc` e
  `aggressive_homoglyphs` começam desativadas. `normalize_spaces` começa ativa.
  A rotina existente preserva sequências emoji e controles linguísticos
  legítimos em seu contexto. Revise o resultado antes de substituir um texto.
- `inspect_file`: acrescenta `report.text_analysis` aos arquivos classificados
  como texto. A inspeção de outros formatos mantém seu comportamento anterior.
- `capabilities`: informa `text_features` com os recursos e o limite de análise.

## Limites

A análise usa no máximo os primeiros 100 mil caracteres e informa quando
houve truncamento. A normalização recusa entradas maiores para nunca devolver
uma cópia cortada. Os rankings mostram até dez termos por categoria. O detalhe
Unicode mostra até cinquenta tipos, com até dez posições por tipo. Contagens
de palavras e frases excluem blocos de código cercados por crases ou tils.

Legibilidade inclui comprimento de frases e palavras, diversidade lexical e
tempo estimado a 200 palavras por minuto. Não atribui uma nota calibrada. As
contagens são aproximadas e abreviações podem ser interpretadas como frases.

A estilometria é uma heurística existente, com marcadores principalmente em
inglês e sem calibração para português. A pontuação não é probabilidade de
autoria humana ou por IA. Os relatórios deixam esse limite explícito.

Gumbel já está implementado sem dependências, mas continua indisponível sem
`WATERMARKS_GUMBEL_KEY`. Não invente uma chave para fazê-lo aparecer como ativo:
o teste exige a chave, tokenização e PRF compatíveis com a geração original.
Nenhuma chave é adicionada ao pacote ou retornada ao ChatGPT.

Modelos MarkLLM, SynthID e de imagem não são instalados por esta mudança.

## Publicação e teste

Depois que o Render implantar a alteração, atualize a conexão MCP no ChatGPT
para descobrir `analyze_text` e `normalize_text`. Os nomes das ferramentas
antigas e o fluxo OAuth permanecem iguais.

Teste com: “Analise este texto com analyze_text, mostre as repetições e os
indicadores de legibilidade sem concluir quem o escreveu”.

Validação local:

```sh
python3 -m unittest discover -s tests -p test_text_analysis.py -v
python3 -m unittest discover -s tests -p test_mcp_oauth.py -v
```
