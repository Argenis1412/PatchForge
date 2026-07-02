# Experiment: Dogfooding 002 — Portfolio Backend

**Date:** 2026-07-02
**Target:** `Portf-lio/backend/` — FastAPI + Pydantic V2
**Issue:** Add Field() metadata to `PhilosophyItemSchema` bare fields (`id`, `name`, `image_url`)
**Run ID:** `run_20260702_042932_5145e2`
**Provider:** claude (claude-sonnet-4-6)

## Locations

```
PatchForge : C:\Users\Visitante\Desktop\Projectos de Github\PatchForge\
Target     : C:\Users\Visitante\Desktop\Projectos de Github\Portf-lio\backend\
Issue file : docs/experiments/dogfooding-002-issue.md
Workspace  : C:\Users\Visitante\.cache\patchforge\workspaces\d3420453496d\
```

## Pipeline Results

| Step | Result | Detail |
|------|--------|--------|
| `scan` | ✅ | 39 hotspots, V1 supported. Required PATH injection for target venv |
| `plan` | ✅ | 1 task, 1 file. $0.01360 (Claude Sonnet 4-6, 788 in / 749 out) |
| `preview` | ❌ | `validation_failed` — patch CRLF mismatch (tool bug, Windows) |
| `apply` (manual) | ✅ | Fix applied manually — QA green |

## Métricas del pipeline *(¿PatchForge funciona?)*

| Métrica | Valor |
|---|---|
| Tiempo total | ~2 min (scan 5s + plan 30s + preview 90s) |
| Coste LLM | $0.018 (plan $0.01360 + executor $0.00443) |
| Status final | `validation_failed` |
| overall_passed | `false` (CRLF bug, no fallo de semántica) |
| Archivos modificados | 1 (`app/schemas/philosophy.py`) |
| Líneas modificadas | +3 / -3 (6 líneas) |

## Métricas del producto *(¿PatchForge genera confianza?)*

| Métrica | Valor |
|---|---|
| ¿El patch resolvió exactamente el issue? | SÍ |
| ¿Hubo cambios fuera del scope? | NO |
| ¿Se aplicó sin edición humana? | NO (1 edit — CRLF tool bug, no corrección lógica) |
| ¿El diff sería aceptado en un PR real? | SÍ (con la edición CRLF) |

## Ediciones humanas

| Tipo | Conteo |
|---|---|
| Formatting | 0 |
| Type hints | 0 |
| Wrong file | 0 |
| Logic | 0 |
| Tool workaround (CRLF) | 1 |

## Root cause

```
Status: validation_failed
Root cause: Tool
Detalle: El executor genera patch.diff con CRLF en Windows. El validation workspace
usa git apply que espera LF. Resultado: "error: patch does not apply".
Nota: La semántica del patch es 100% correcta.
```

## Bugs descubiertos (registrar en discoveries.md)

1. **CRLF en patch.diff (Windows)**: El executor escribe el patch con `\r\n`.
   `git apply` en el validation workspace falla por mismatch de line endings.
   Reproducible: siempre en Windows cuando el archivo fuente usa LF.

2. **Git root mismatch**: El scan opera desde `backend/` pero el git root es `Portf-lio/`.
   El patch usa `--- a/app/schemas/philosophy.py` pero `git diff` muestra
   `backend/app/schemas/philosophy.py`. Un `git apply` desde la raíz del repo fallaría.
   El isolation workspace de preview mitiga esto (opera desde la copia temporal de `backend/`),
   pero `patchforge apply` podría tener el mismo problema.

## Criterio de éxito

**Éxito funcional:**
- [x] Patch semánticamente correcto
- [ ] `status = "previewed"` — FALLO (CRLF bug)
- [x] El repo target no tiene cambios inesperados

**Éxito de producto:**
- [ ] 0 ediciones humanas (1 edit de CRLF workaround)
- [x] Solo `app/schemas/philosophy.py` modificado

## Post-fix QA (manual apply)

```
ruff check app/     → ✅ All checks passed
ruff format --check → ✅ 57 files already formatted
pytest tests/ -q    → ✅ 134 passed, 3 skipped, coverage 81.58%
git diff --stat     → backend/app/schemas/philosophy.py | 6 +++---
```

## Verdict

```
Issue:
Add Field() metadata to PhilosophyItemSchema (id, name, image_url)

Pipeline reliability:
FAIL — validation_failed por CRLF (tool bug, no fallo de lógica)

Patch quality:
PASS — diff semánticamente correcto, scope exacto, QA verde

Would I merge this PR exactly as generated?
YES (una vez corregido el CRLF — no corrección de lógica necesaria)

Reason:
El LLM generó exactamente lo que se pidió: Field() con description y max_length
apropiados para cada campo. El plan fue perfecto (1 tarea, 1 archivo, sin scope
creep). El único bloqueo fue infraestructura Windows (CRLF). Si se corre en Linux/Mac
o se agrega un fix de CRLF al executor, este experimento habría sido 100% automatizado.
```

## Lecciones para PatchForge

1. El executor debe normalizar line endings (LF) antes de escribir `patch.diff`
2. El validation workspace debe usar `git apply --whitespace=fix` o pre-procesar el patch
3. El git root mismatch es un riesgo latente para `patchforge apply` en repos con backend/ como subdirectorio
4. El plan (Architect) fue excelente: 1 tarea exacta, 0 scope creep, descripción precisa
5. La detección del proveedor funcionó: scan y plan sin problema, gemini quota fallback a claude
