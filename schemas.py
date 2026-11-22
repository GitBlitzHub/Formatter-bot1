from typing import List, Literal
from pydantic import BaseModel, Field


class Person(BaseModel):
    """Одна запись о человеке, как её вернула LLM (ещё не нормализованная)."""

    title: str = Field("", description="Mr/Mrs/Ms/Miss/Dr/Prof/Sir/Dame/Lord/Lady/Mx или пусто")
    gender: Literal["M", "F", "U"] = Field("U", description="M/F, 'U' если не определить")
    first_name: str = Field("", description="Только имя, без титула и фамилии")
    middle_names: str = Field("", description="Средние имена/инициалы через пробел")
    last_name: str = Field("", description="Только фамилия")
    dob: str = Field("", description="Дата рождения строго дд.мм.гггг")
    dl_number: str = Field("", description="Номер водительских прав или пусто")
    address: str = Field("", description="Адрес одной строкой: улица, город, ПОСТКОД")


class Extraction(BaseModel):
    people: List[Person]