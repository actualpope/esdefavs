# Changelog

## 0.6.14 — 2026-07-07

- `update.sh` kan nå oppdatere uten `git` ved å laste ned GitHub ZIP fra `main`.
- README beskriver installasjon fra GitHub ZIP, ikke bare git-klone.
- Kontrollpanelets `Oppdater programmet fra GitHub` fungerer nå også på Steam Deck-oppsett uten git installert.

## 0.6.13 — 2026-07-07

- Legger til `Oppdater programmet fra GitHub` i kontrollpanelet.
- `install.sh` lagrer nå hvor GitHub-klonen ligger, slik at installert `update.sh` kan kjøre `git pull` fra riktig mappe.
- README beskriver GitHub-basert installasjon og oppdatering uten nye ZIP-filer.

## 0.6.12 — 2026-07-07

- Fjerner gammel `watch-autosync` polling-kode fra CLI og autosync-modulen.
- Skriver nå `applied.json` etter vellykket reconcile, slik at `plan`/`check` kan sammenligne mot sist bekreftet sync.
- Legger til `update.sh` for enkel `git pull` + reinstall på Steam Deck.
- Legger til GitHub Actions-testworkflow som kjører unittest-pakken på push og pull request.

## 0.6.11 — 2026-07-07

- Fjerner timer-/periodisk-sjekk-modellen fra ny autosync-retning.
- Legger til `esde-closed` som eksplisitt trigger for “ES-DE ble lukket”.
- Når `esde-closed` kjøres, leses favoritter én gang. Hvis Steam kjører, blir endringen liggende som pending.
- Autosync-service er nå en startup/pending-sjekk, ikke en kontinuerlig watcher.

## 0.6.10 — 2026-07-07

- Installering/oppgradering restarter nå autosync-servicen hvis den allerede kjører.
- Dette hindrer at manuell knapp bruker ny kode mens bakgrunnsservicen fortsatt kjører gammel prosess.
- Status viser nå `Last check` og om Steam sist ble sett som `running`, `stopped` eller `unknown`.

## 0.6.9 — 2026-07-07

- Gjør Steam-shortcut cleanup mer tolerant for hvordan SRM faktisk skriver `shortcuts.vdf`.
- Fjerning matcher nå på spillnavn og normalisert `LaunchOptions`/ROM-start, ikke bare helt eksakt intern shortcut-nøkkel.
- Dette gjør at spill kan fjernes selv om SRM har skrevet feltene med andre anførselstegn eller uten intern `ES-DE Favorites Sync`-tag.

## 0.6.8 — 2026-07-06

- `Oppdater Steam nå` tvinger nå en full Steam/SRM-rekonsiliering selv om ES-DE-favorittlisten ikke har endret seg.
- Autosync sjekker nå Steam-biblioteket direkte før den konkluderer med “ingen endring”.
- Hvis Steam mangler en favoritt som ligger i dagens SRM-manifest, kjører programmet oppdatering på nytt.
- Hvis Steam fortsatt har en tidligere favoritt som ikke lenger finnes i dagens manifest, kjøres cleanup/oppdatering på nytt.

## 0.6.7 — 2026-07-06

- Renser gamle `ES-DE Favorites`-collections fra eksisterende `ES-DE Favorites Sync`-parsere ved neste staging.
- Bevarte parsere får nå samme category-cleanup som nye parsere.
- Hoved-GUI viser nå én samlet `Oppdater Steam nå`-handling i stedet for separate add/remove-knapper.

## 0.6.6 — 2026-07-06

- Beholder `ES-DE Favorites Sync`-parserne i SRM selv når en konsoll ikke har noen aktive favoritter.
- Parserens manifest skrives da som en tom liste i stedet for at parseren fjernes fra SRM-oppsettet.
- Dette gir SRM et stabilt parser-anker for senere add/remove og gjør oppførselen mer forutsigbar.

## 0.6.5 — 2026-07-06

- Legger til direkte stale-cleanup av Steam-shortcuts som tidligere ble importert av favorittsync, men ikke lenger finnes i ES-DE favorites.
- Programmet lagrer nå siste SRM-entry-liste under state og bruker den til å rydde gamle favoritter ved neste sync.
- Første kjøring etter oppgradering bruker eksisterende SRM-manifestmappe som forrige liste hvis egen state ikke finnes.
- SRM `remove` kjøres fortsatt, men stopper ikke lenger hele syncen hvis den ikke rydder opp alene.

## 0.6.4 — 2026-07-06

- Kjører nå SRM `remove` før ny staging og SRM `add`, slik at spill som fjernes fra ES-DE favorites også fjernes fra Steam.
- `remove` kjøres på eksisterende `ES-DE Favorites Sync`-parsere før manifestene overskrives med ny favorittliste.
- Legger til `srm-remove-now` i CLI og grafisk meny.
- Status viser pending/siste SRM remove.

## 0.6.3 — 2026-07-06

- Fjerner ekstra Steam collection `ES-DE Favorites` fra genererte SRM Manual-parsere.
- Favorittspill arver nå bare konsollens originale SRM-kategori, slik SRM ellers organiserer biblioteket.

## 0.6.2 — 2026-07-06

- Finner Steam ROM Manager flere steder, inkludert `~/Applications`, `~/Downloads`, `~/Desktop`, EmuDeck-verktøymapper og direkte i `.../Emulation/tools/` på SD-kort.
- Legger til GUI-valget `Velg SRM AppImage`, slik at brukeren kan peke på SRM manuelt én gang hvis auto-detection feiler.
- Legger til CLI-kommandoen `set-srm-path`.
- Compatibility-report viser nå SRM-kandidater og override-filen som brukes.
- SRM-runner kan også prøve Flatpak-ID-en `com.steamgriddb.steam-rom-manager` hvis AppImage ikke finnes.

## 0.6.1 — 2026-07-06

- Fikser siste ledd: programmet kjører nå Steam ROM Manager sin CLI-kommando `add` etter staging når Steam er lukket.
- Mens SRM `add` kjøres, aktiveres bare parserne som eies av `ES-DE Favorites Sync`; andre SRM-parsere restores etterpå.
- Beholder direkte Steam-shortcuts-import som fallback hvis SRM CLI ikke kan kjøres.
- Legger til `srm-add-now` i CLI og grafisk meny.

## 0.6.0 — 2026-07-06

- Legger til grafisk kontrollpanel i programmappen: `EmuDeck Favorites Sync.desktop` / `EmuDeck Favorites Sync.sh`.
- Legger til siste automatiske ledd: etter SRM-staging importeres favorittene til Steam `shortcuts.vdf` når Steam er lukket.
- Steam-importen bevarer eksisterende shortcuts og erstatter bare shortcuts merket med `ES-DE Favorites Sync`.
- Status viser nå også pending Steam-import og sist importtidspunkt.
- Ny feilsøkingskommando: `steam-import-now`.

## 0.5.0 — 2026-07-06

- Legger til autosync med bruker-service (`systemd --user`) som kan skrus på og av.
- Ny enkel bruk: `autosync-on`, `autosync-off`, `autosync-status` og `autosync-now`.
- Autosync oppdager endringer i ES-DE-favoritter i bakgrunnen. Hvis Steam kjører, settes endringen som pending og SRM-staging skrives først når Steam er lukket.
- Status viser om sync er på, om service kjører, om endringer venter, sist oppdagede favorittendring, sist sync og nåværende favoritter.
- Legger ved enkle scripts i programmappen: `sync-on.sh`, `sync-off.sh`, `sync-status.sh` og `sync-now.sh`.

## 0.4.0 — 2026-07-06

- Legger til anbefalt `sync`-kommando: scan + dry-run, og `sync --confirm` for staging.
- Oppdaterer install-melding og README for faktisk stabil testbruk.
- Beholder alle sikkerhetssperrer fra 0.3.x: Steam må være lukket ved confirm, backup før skriving, ingen global nuke, og kun egne parser-ID-er endres.

## 0.3.2 — 2026-07-06

- Matcher Wii U/Cemu-parsere som bruker EmuDeck-layouten `${romsdirglobal}/wiiu/roms/`.

## 0.3.1 — 2026-07-06

- Tillater apply-staging av gyldige favoritter selv om en annen favoritt peker til manglende ROM.
- Slike situasjoner vises som `REMOVAL_NOT_SAFE` warning i stedet for å blokkere add/stage.

## 0.3.0 — 2026-07-06

- Legger til første sikre `apply`: `--dry-run` validerer, `--confirm` skriver SRM Manual-manifests og egne SRM-parsere.
- Blokkerer `apply --confirm` hvis Steam kjører, hvis SRM `deleteDisabledShortcuts` er aktivert, eller hvis scan/preview ikke er trygg.
- Lager backup av SRM-konfig og egne manifestmapper før skriving.
- Eies via parser-ID-prefix `emudeck-favorites-sync:` og rører ikke eksisterende EmuDeck-parserblokker.

## 0.2.1 — 2026-07-06

- Forbedrer SRM-preview-match for systemer der EmuDeck-parseren bruker global `${romsdirglobal}` og systemmappen ligger i glob-mønsteret, for eksempel GBA.
- Legger til system-aliaser som `gba` → `Nintendo Game Boy Advance`.

## 0.2.0 — 2026-07-06

- Legger til `srm-preview`, som lager en trygg SRM-preview under programmets egen state-mappe.
- Matcher favoritter mot eksisterende EmuDeck/SRM-parsere ved systemets ROM-mappe.
- Fortsetter å blokkere `apply`; ingen Steam-, SRM-, EmuDeck- eller ES-DE-konfigurasjon endres.

## 0.1.1 — 2026-07-06

- Leser ES-DE `gamelist.xml` mer tolerant når filen inneholder flere toppnivå-elementer eller XML-fragmenter.
- Marker fragment-gjenoppretting som advarsel i stedet for å blokkere hele systemet som malformed XML.

## 0.1.0 — 2026-06-18

- Første Steam Deck-testversjon.
- Read-only ES-DE scanner og ROM-resolver.
- Auto-detection av standard EmuDeck-/ES-DE-paths og flyttbar lagring.
- Desired/applied diff med removal-sperrer.
- Doctor, scan, plan, check, status og compatibility-report.
- Steam/SRM apply er eksplisitt deaktivert.
- Standardbibliotek-only; ingen nettverk eller tredjepartsavhengigheter.
