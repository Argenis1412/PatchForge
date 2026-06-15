# Refactorização — Plano de Fases

> **Objetivo:** Melhorar estabilidade, manutenção e legibilidade para agentes de IA.
> **Regra de ouro:** Zero mudanças de comportamento. Extrair, não reescrever.

---

## Fase 0 — Baseline ✅ (concluída)

**Commit:** `731b8d0` — `chore(baseline): capture quality and behavior baseline`

### O quê
- Characterization tests para comandos offline (`doctor`, `scan`)
- Baseline de QA: `ruff check`, `ruff format`, `pytest` (330 pass, 2 skip)
- Métricas estruturais: ficheiros >500 linhas, funções >50/100 linhas, docstrings, type annotations
- Script `scripts/baseline_metrics.py` para gerar `docs/baseline/metrics.json`

### Métricas capturadas

| Métrica | Valor |
|---------|-------|
| Total ficheiros Python | 77 |
| Ficheiros >500 linhas | 6 |
| Ficheiros >1000 linhas | 0 |
| Funções >100 linhas | 11 |
| Funções >50 linhas | 30 |
| Public funcs sem docstring | 350 |
| Public funcs sem type annotation | 343 |
| Cobertura de testes | 90% |

### Estrutura criada
```
docs/baseline/
├── ruff_check.txt
├── ruff_format.txt
├── pytest.txt
├── coverage.txt
└── metrics.json
scripts/baseline_metrics.py
tests/test_baseline_cli.py
```

---

## Fase 1 — `agents/scout.py` → `agents/scout/`

**Risco:** Baixo — módulo interno com poucos consumidores.

### Extração
| Ficheiro | Conteúdo |
|----------|----------|
| `agents/scout/__init__.py` | Re-exporta `run()`, `run_from_issue()` |
| `agents/scout/provider.py` | `call_gemini()` (7 parâmetros) |

### Não fazer
- ❌ Mudar API pública (`run()`, `run_from_issue()`)
- ❌ Renomear funções
- ❌ Alterar comportamento de provider

---

## Fase 2 — `agents/architect.py` → `agents/architect/`

**Risco:** Baixo — módulo interno.

### Pré-requisito
- ✅ Test snapshot do prompt ANTES de extrair

### Extração
| Ficheiro | Conteúdo |
|----------|----------|
| `agents/architect/__init__.py` | Re-exporta `run()`, `run_from_issue()` |
| `agents/architect/prompts.py` | Templates inline atualmente no módulo |
| `agents/architect/provider.py` | `call_claude()` (7 parâmetros) |

---

## Fase 3 — `agents/validator.py` → `agents/validator/`

**Risco:** Baixo — módulo interno.

### Extração
| Ficheiro | Conteúdo |
|----------|----------|
| `agents/validator/__init__.py` | Re-exporta `run()` |
| `agents/validator/runners.py` | `run_ruff()`, `run_pytest()`, `run_tsc()`, `_run()` |

---

## Fase 4 — `agents/executor.py` → `agents/executor/` ⚠️ 70% do risco

**Risco:** Alto — concentra provider failover, circuit breaker, DAG scheduling, task application.

### Extração
| Ficheiro | Conteúdo |
|----------|----------|
| `agents/executor/__init__.py` | Re-exporta `run()` |
| `agents/executor/providers.py` | Provider orchestration, circuit breaker |
| `agents/executor/scheduler.py` | `_build_dag()`, `_topological_order()` |
| `agents/executor/applier.py` | `_apply_task()`, lógica de task application |
| `agents/executor/rollback.py` | `rollback_to_commit()`, revert |
| `agents/executor/diffing.py` | Geração de diff consolidado |

### Estratégia
- Extrair um ficheiro de cada vez
- QA gate entre cada extração
- Testes existentes (`test_executor.py`, `test_executor_scheduler.py`) validam cada extração

---

## Fase 5 — `main.py` → `commands/apply.py`

**Risco:** Médio — CLI pública, mas já existe padrão `commands/*.py`.

### Extração
| Ficheiro | Conteúdo |
|----------|----------|
| `commands/apply.py` | `apply()` (406 linhas — maior função do projeto) |
| Helper | `_load_target_config()` para ficheiro partilhado |
| `main.py` | ~100 linhas (só definição Typer + delegação) |

---

## Fase 6 — Docstrings + Types + `__all__`

**Risco:** Baixo — sem alteração de comportamento.

| Prioridade | O quê | Onde |
|-----------|-------|------|
| Alta | Docstrings | `commands/`, `agents/`, interfaces públicas |
| Média | Type annotations | `main.py` (return types), `clients/*.py` |
| Selectivo | `__all__` | Só `agents/`, `commands/`, `schemas/` (não obrigatório em todos) |

---

## Fase 7 — Cleanup final

- Verificar imports em todos os ficheiros (sem importações circulares)
- `ruff check .` → 0 errors
- `ruff format --check .` → clean
- `pytest` → mesmos resultados da baseline

---

## Pós-refactor — `scanners/quality.py`

> **Nota:** Só depois de todas as fases de refactor estarem completas e estáveis.

### Design (planeado)
- Segue padrão de `scanners/python.py` (determinista, `ast`, `os.walk`)
- Output: Pydantic `QualityReport` consumível por qualquer agente AI
- 12 checks em 4 dimensões:
  1. Legibilidade: type annotations, docstrings, `__all__`
  2. Complexidade: funções longas, complexidade ciclomática, nesting depth, file length
  3. Segurança: bare except, mutable defaults
  4. Higiene: TODO/FIXME, dead imports
- Severidade gradual: medium (500-700), high (700-1000), critical (1000+)
- `recommendation` + `line` no output para consumo direto por IA

---

## Regras de QA (antes de cada commit)

```bash
ruff check .             # must return 0 errors
ruff format --check .    # must return clean
pytest -v                # 330 passed, 2 skipped (baseline)
```

Formato de commit: `<type>(<scope>): <message>` (Inglês)
