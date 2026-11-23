import asyncio, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from normalizer import build_block
from schemas import Person
async def main():
    # LLM «ошиблась»: фамилия в начале, а first_name = Deakin
    p = Person(gender="F", name_tokens=["Deakin","Judith","Ann"], first_name="Deakin", middle_names="Judith",
               last_name="Ann", surname_guess="Ann", dob="18/11/1967", dl_number="deaki661187ja9gy 39",
               address="35 serpentine rd kendal la94pe", addr_number="35", addr_street="serpentine rd",
               addr_town="kendal", addr_postcode="la94pe")
    print(await build_block(p, "Deakin Judith Ann DEAKI661187JA9GY 39"))
    print("-----")
    p = Person(name_tokens=["John","SMITH"], surname_guess="SMITH", address="1 Fake Street, Kendal, LA9 4PE",
               addr_number="1", addr_street="Fake Street", addr_town="Kendal", addr_postcode="LA9 4PE")
    print(await build_block(p, ""))
asyncio.run(main())
