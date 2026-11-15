# Rinkos žemėlapio dizaino pavyzdžio šaltiniai

Darbiniai failai, iš kurių sugeneruoti tamsusis (brėžinio fono) ir trečiasis šviesus rinkos žemėlapio variantai Design drobėje. Tai dizaino pavyzdys su 2026-11-12 duomenimis, ne gamybinis valdiklis.

- `variantas-tamsus.py` – artboard'o generatorius (skaičiai įrašyti ranka; keliai į failus – kaip darbo aplanke).
- `variantas-pagal-pastabas.py` – šviesaus trečiojo varianto generatorius (pagal kolegės pastabas, 2026-11-14).
- `variantas-ketvirtas.py` – ketvirtojo varianto generatorius: antrojo stilius, trečiojo struktūra, importo žiedas Lietuvos kontūre (2026-11-14).
- `žemėlapio-kontūrai.svg.txt` – šalių kontūrai (Natural Earth 1:50m, Mercator, kadras 760 × 570).
- `trasos.mjs`, `trasos.json` – jungčių trasų taškai ir jų projekcija į kadrą: NordBalt (Klaipėda–Nybro), LitPol Link (Alytus–Ełk), keturios 330 kV linijos į Latviją. Trasos apytikslės – pagal viešus žemėlapius ir galinių pastočių vietas.
- `nepaaiškintos-dalies-patikra.py` – patikra, ar papildomi Baltijos regiono veiksniai sumažina modelio nepaaiškintą dalį.
