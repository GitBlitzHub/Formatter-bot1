import asyncio, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from address import verify_address
C=[("35 Serpentine Road Kendal LA9 4PE",dict(number="35",street="Serpentine Road",town="Kendal",postcode="LA9 4PE")),
("35 Serpentin Rd kendal la94pe",dict(number="35",street="Serpentin Rd",town="Kendal",postcode="la94pe")),
("99 Serpentine Road Kendal LA9 4PE",dict(number="99",street="Serpentine Road",town="Kendal",postcode="LA9 4PE")),
("35 Serpentine Road Kendal LA9 4PD (чужой посткод)",dict(number="35",street="Serpentine Road",town="Kendal",postcode="LA9 4PD")),
("35 Serpentine Road Kendal (без посткода)",dict(number="35",street="Serpentine Road",town="Kendal")),
("35 Serpentine Road Kendal ZZ1 1ZZ",dict(number="35",street="Serpentine Road",town="Kendal",postcode="ZZ1 1ZZ")),
("12 Fantasy Lane Kendal LA9 4PE",dict(number="12",street="Fantasy Lane",town="Kendal",postcode="LA9 4PE")),
]
async def m():
    for raw,kw in C:
        r=await verify_address(raw,**kw); print(raw,"=>",r.status,"|",r.formatted,"|",r.notes,flush=True)
asyncio.run(m())
