from typing import List, Literal
from pydantic import BaseModel, Field


class Person(BaseModel):
    """Одна запись о человеке, как её вернула LLM (ещё не нормализованная)."""

    title: str = Field(
        "",
        description="Mr/Mrs/Ms/Miss/Dr/Prof/Sir/Dame/Lord/Lady/Mx или пусто",
    )
    gender: Literal["M", "F", "U"] = Field(
        "U",
        description="Пол: M или F. 'U' если определить не удалось.",
    )
    first_name: str = Field(
        "",
        description="Только имя, без титула и фамилии",
    )
    middle_names: str = Field(
        "",
        description="Средние имена/инициалы через пробел, пусто если их нет",
    )
    last_name: str = Field(
        "",
        description="Только фамилия, включая составные (Van Der Berg, O'Brien-Smith)",
    )
    dob: str = Field(
        "",
        description="Дата рождения строго в формате дд.мм.гггг, пусто если нет даты",
    )
    dl_number: str = Field(
        "",
        description="Номер водительских прав, пусто если нет",
    )
    address: str = Field(
        "",
        description="Адрес одной строкой: улица, город, ПОСТКОД",
    )


class Extraction(BaseModel):
    people: List[Person]