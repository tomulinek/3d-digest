# 3d-digest
Automatický výběr toho nejzajímavějšího z aditivní výroby. 

Data pro web [3damuse.com](https://3damuse.com/): jeden soubor `news_RRRR_MM.json` na měsíc vydání a přehled `news_index.json`.

## Generátor

Kód je ve složce `generator/`. Spouští ho GitHub Actions (workflow **Novinky**) 4× denně.

Nastavení repozitáře (Settings → Secrets and variables → Actions):

- secret `GEMINI_API_KEY` – klíč ke Gemini API (povinné),
- variable `GEMINI_MODEL` – jiný model než výchozí `gemini-4-hydrogen` (volitelné).

Ruční spuštění: Actions → Novinky → Run workflow:

- `run` – běžný běh,
- `add-url` + adresa – ručně přidá jeden článek,
- `clean` – přerovná archiv podle měsíce vydání a odstraní duplicity,
- `smoke-test` – jedno zkušební volání Gemini, nic nezapisuje,
- `backfill-region` + měsíc `RRRR-MM` – doplní kód oblasti (`region`) článkům od daného měsíce, které ho nemají,
- `recheck-world-region` + měsíc `RRRR-MM` – totéž a navíc znovu posoudí články s oblastí `WORLD` (po změně pravidla),
- `fix-terminology` + měsíc `RRRR-MM` – opraví české názvosloví 3D tisku v titulcích a bodech článků od daného měsíce podle slovníku `GLOSSARY` v generátoru (mění jen `title` a `summary_bullets_cs`) a článkům o značce Prusa nastaví oblast `CZ`.

Oblast článku je pole `region`: kód země podle ISO 3166-1 alpha-2 (`CZ`, `DE`, `US`…), nebo zóna `EUROPE`, `AMERICAS`, `ASIA`, `AFRICA`, `OCEANIA`, `WORLD`. Nahrazuje starší textová pole `region_cs` a `region_en`. Oblast se určuje podle smyslu a dopadu novinky: země nebo region jmenovaný v titulku či tématu (China Watch → `CN`), výstavy podle země konání (Formnext → `DE`), `WORLD` jen u jasně globálního dopadu; u výzkumu a při nejistotě země nebo kontinent, odkud novinka pochází.

Místně: `python generator/generate_news_json_github.py --dry-run SLOŽKA` zapisuje jen do složky, ne na GitHub.
