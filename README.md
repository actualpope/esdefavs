# SRM Sync 1.4

Et lite Steam Deck-program for å legge spill fra EmuDeck-rom-mappa inn i Steam via Steam ROM Manager (SRM). Det brukes ikke lenger ES-DE-favoritter; alt velges i programvinduet.

## Slik ser det ut

- **ROMS** (venstre): alle spill i rom-mappa, med konsoll (mappenavnet) og filnavn, sortert etter konsoll og så alfabetisk. Knappen **Velg rom-mappe …** bytter mappe.
- **SRM** (høyre): spillene som ligger i Steam via våre egne SRM-parsere (`SRM Sync - …`, én per konsoll). Knappen **SRM-oppsett …** viser om SRM er riktig koblet, og hvilken emulator hver konsoll bruker.
- **→ / ←**: flytter valgte spill mellom listene (også dobbeltklikk). Ingen filer flyttes; det er bare et valg. Endringer vises som «ny» (grønn) eller «fjernes» (rød) til du trykker Lagre.
- **Lagre** (grå til du har gjort endringer): lukker Steam, kjører SRM for konsollene som er endret, sier hvor mange spill som ble lagt til, og starter Steam igjen.
- **Tilbakestill**: angrer alle flyttinger som ikke er lagret ennå.
- **Fiks**: oppdaterer startinnstillingene (emulator og argumenter) for alle spill i SRM-lista etter dagens oppsett. Legger ikke til og fjerner ikke spill. Navn du har endret i Steam beholdes.
- **Oppdater program**: lukker programmet, henter siste versjon fra GitHub og åpner programmet igjen med beskjed om hvordan det gikk.
- Spill du flytter, ligger øverst i listene til du trykker Lagre.

Kjør programmet i Desktop Mode. Steam lukkes mens Lagre/Fiks pågår.

## Viktig å vite

- Spill som lå i Steam fra tidligere versjoner blir hentet inn automatisk første gang og ligger urørt i Steam.
- Et spill som flyttes ut og lagres, forsvinner fra Steam sammen med spilletid og bilder der.
- Konsollnavnet er navnet på mappa spillet ligger i. Mappa `gamecube` hoppes over; GameCube-spill ligger i `gc`.
- Wii U viser bare `.wua`-filer. PS3-spill er mapper og vises som ett spill per mappe.
- Multi-disk-spill i en `Spill.m3u`-mappe vises som ett spill. For PS2 (PCSX2 støtter ikke `.m3u`) vises hver disk for seg.
- Hvilken emulator et nytt spill får, bestemmes av konsollens vanlige EmuDeck-parser i SRM. Er det flere, velges den under **SRM-oppsett**.
- Før hver endring tas sikkerhetskopi av SRM-oppsettet og Steams `shortcuts.vdf` i `~/.local/state/srm-sync/backups/`.

## Installer

Last ned ZIP én gang, og bruk deretter **Oppdater program** i vinduet.

1. Last ned `https://github.com/actualpope/esdefavs/archive/refs/heads/main.zip` på Steam Deck og pakk den ut, for eksempel i `Downloads`.
2. Åpne Konsole og kjør:

```bash
cd ~/Downloads/esdefavs-main
bash install.sh
```

3. Start **SRM Sync** fra skrivebordet.

## Kommandolinje

Vinduet er det vanlige. For feilsøking finnes også:

```bash
~/.local/bin/srm-sync status   # rom-mappe, SRM og spill
~/.local/bin/srm-sync report   # lagrer en feilsøkingsrapport
~/.local/bin/srm-sync update   # samme som Lagre
~/.local/bin/srm-sync fix      # samme som Fiks
```

Logg fra siste SRM-kjøring: `~/.local/state/srm-sync/logs/last-run.txt`.

## Avinstaller

```bash
bash ~/.local/share/srm-sync/uninstall.sh
```

Spillene i Steam og SRM blir ikke rørt.

## Utvikling

```bash
python3 -m unittest discover -s tests -t .
```

Testene bruker en liten SRM-etterligning (`tests/fake_srm.py`) som følger SRM sine regler for legg til, oppdatering og fjerning.
