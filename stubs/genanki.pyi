from typing import TypedDict

class FieldDef(TypedDict):
    name: str

class TemplateDef(TypedDict):
    name: str
    qfmt: str
    afmt: str

class Model:
    def __init__(
        self,
        model_id: int,
        name: str,
        fields: list[FieldDef],
        templates: list[TemplateDef],
        css: str = ...,
    ) -> None: ...

class Note:
    def __init__(
        self,
        model: Model,
        fields: list[str],
        guid: str | None = ...,
    ) -> None: ...

class Deck:
    def __init__(self, deck_id: int, name: str) -> None: ...
    def add_note(self, note: Note) -> None: ...

class Package:
    def __init__(self, deck_or_decks: Deck | list[Deck]) -> None: ...
    def write_to_file(self, file: str) -> None: ...

def guid_for(*args: str) -> str: ...
