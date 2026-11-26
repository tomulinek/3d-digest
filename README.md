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
- `smoke-test` – jedno zkušební volání Gemini, nic nezapisuje.

Místně: `python generator/generate_news_json_github.py --dry-run SLOŽKA` zapisuje jen do složky, ne na GitHub.
