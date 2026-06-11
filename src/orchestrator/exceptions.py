class LLMParseError(Exception):
    # TODO: migrate to PatchForgeError in T-07

    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__(text)


class SchemaValidationError(Exception):
    # TODO: migrate to PatchForgeError in T-07

    def __init__(self, text: str, schema: type) -> None:
        self.text = text
        self.schema = schema
        super().__init__(f"Schema validation failed for {schema.__name__}: {text[:200]}")
