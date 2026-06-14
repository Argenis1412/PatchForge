# CS-001: Replace hardcoded schema_version default with CURRENT_SCHEMA_VERSION

## Metadata

| Campo | Valor |
|-------|-------|
| **Issue** | `issues/001-use-constant-for-schema-default.md` |
| **Target repo** | PatchForge (Clon_PatchForge) |
| **Date** | 2026-06-13 |
| **Experiment** | 001 — Clone Workflow POC |
| **Branch** | `feat/experiment-001-dogfooding` |

## Results

| Métrica | Valor |
|---------|-------|
| Archivos modificados (semántica) | 1 (`artifacts.py`) |
| Archivos de infraestructura | 2 (`executor.py`, `orchestrator.json`) |
| Líneas cambiadas | 1 (semántica) + 6 (infraestructura) |
| Tests ejecutados | 288 |
| Tests fallados | 0 |
| Ruff | 0 errores |
| Costo LLM total | ~$0.034 (3 runs de plan por iteraciones) |
| Tiempo humano | ~20 min (supervisión + debugging dels 3 bugs) |
| Tiempo pipeline (scan → apply) | ~2 min |
| Bugs descubiertos durante el experimento | 3 |

## Bugs descubiertos

| # | Bug | Impacto | Fix |
|---|-----|---------|-----|
| 1 | `lineterm=""` en `difflib.unified_diff()` — cabeceras del diff pegadas sin newline | Patch corrupto (`git apply` rechaza) | Remover `lineterm=""` en `executor.py:414` |
| 2 | LLM devuelve archivo sin trailing newline — difflib genera hunk no-op | `git apply` rechaza como "corrupt patch" | Normalizar trailing newline en `executor.py:354-356` |
| 3 | `ruff`/`pytest` no resueltos en PATH durante post-apply validation | Rollback automático (T-02 funcionó) | `orchestrator.json` con rutas absolutas al `.venv` del target |

## Lecciones

1. **El pipeline dogfooding funciona.** PatchForge logró planear, ejecutar, validar y aplicar un cambio real sobre un clon de sí mismo sin intervención humana en la ejecución.
2. **Los bugs aparecen donde menos los esperas.** El formato de diff (`difflib`) y los newlines fueron los problemas reales, no la lógica del LLM.
3. **T-02 (Atomic Rollback) salvó el experimento.** Cuando el post-apply validation falló por PATH, el clon volvió a estado limpio automáticamente.
4. **T-01 (Path Traversal Hardening) protegió el clon.** El workspace externo impidió cualquier fuga al sistema.
5. **`orchestrator.json` es necesario para targets sin herramientas globales.** Sin rutas absolutas, `ruff`/`pytest` no se encuentran.

## Timeline

| Hito | Tiempo |
|------|--------|
| Preparación (clone + venv + issue file) | ~6 min |
| 1er intento: scan → plan → preview → apply | ~2 min rotos por bug #1 y #2 |
| Debug + fixes (bug #1 y #2) | ~10 min |
| Configurar PATH (orchestrator.json + bug #3) | ~3 min |
| 2do intento completo | ~2 min — **éxito** |
| **Total** | **~23 min** |
