---
title: Add Field() metadata to PhilosophyItemSchema bare fields
severity: low
labels: schema, pydantic
---

## Objective

Add `Field()` with description and constraints to the three bare fields in `PhilosophyItemSchema` that are missing it (`id`, `name`, `image_url`). The other two fields (`role` and `description`) already have `Field()`. Only modify `app/schemas/philosophy.py`.

## Current state

```python
class PhilosophyItemSchema(BaseModel):
    id: str                  # missing Field()
    name: str                # missing Field()
    role: dict[str, str] = Field(..., description="Role or title in multiple languages")
    image_url: str           # missing Field()
    description: dict[str, str] = Field(..., description="Detailed description in multiple languages")
```

## Target state

```python
class PhilosophyItemSchema(BaseModel):
    id: str = Field(..., description="Unique identifier", max_length=50)
    name: str = Field(..., description="Name of the philosopher", max_length=100)
    role: dict[str, str] = Field(..., description="Role or title in multiple languages")
    image_url: str = Field(..., description="URL of the philosopher's image")
    description: dict[str, str] = Field(..., description="Detailed description in multiple languages")
```

## Scope

Only `app/schemas/philosophy.py` — zero other files.

## Verification

- `ruff check app/schemas/` must pass
- `pytest tests/ -q` must pass
- Only `app/schemas/philosophy.py` is modified
