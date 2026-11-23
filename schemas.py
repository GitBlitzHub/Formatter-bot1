from typing import List, Literal
from pydantic import BaseModel, Field, ValidationInfo, field_validator


class Person(BaseModel):
    """Одна запись, как её вернула LLM (ещё не проверенная кодом)."""

    title: str = ""
    gender: Literal["M", "F", "U"] = "U"

    # ФИО: LLM даёт только СЫРЬЁ. Итоговое разделение делает names.py.
    name_tokens: List[str] = Field(
        default_factory=list,
        description="Все слова ФИО в порядке ввода, без титула",
    )
    surname_guess: str = ""        # какая подстрока, по мнению LLM, фамилия
    surname_marker: Literal["label", "comma", ""] = ""
    first_name: str = ""           # запасной вариант, если name_tokens пуст
    middle_names: str = ""
    last_name: str = ""

    dob: str = ""
    dl_number: str = ""

    # Адрес: сырая строка + разобранные части (для проверки)
    address: str = ""
    addr_flat: str = ""            # Flat 2 / Apartment 5 / название дома
    addr_number: str = ""          # номер дома: 35, 12A
    addr_street: str = ""
    addr_town: str = ""
    addr_postcode: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def _coerce_llm_values(cls, v, info: ValidationInfo):
        """I-03: LLM иногда шлёт null / неожиданные значения — не роняем весь пакет."""
        if info.field_name == "name_tokens":
            if v is None:
                return []
            if isinstance(v, list):
                return [x for x in v if x is not None]
            return v
        if info.field_name == "gender":
            g = v.strip().upper() if isinstance(v, str) else ""
            return g if g in ("M", "F", "U") else "U"
        if info.field_name == "surname_marker":
            mk = v.strip().lower() if isinstance(v, str) else ""
            return mk if mk in ("label", "comma", "") else ""
        return "" if v is None else v


class Extraction(BaseModel):
    people: List[Person]
