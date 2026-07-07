# EmuDeck Favorites Sync 0.6.12

Et lite Steam Deck-program som syncer ES-DE-favoritter til Steam.

Programmet gjør tre ting:

1. Leser favoritter fra ES-DE `gamelist.xml`.
2. Lager/oppdaterer egne SRM Manual-parsere med navn som `ES-DE Favorites Sync - ...`.
3. Når Steam er lukket, kjører det Steam ROM Manager `remove` og deretter `add` automatisk for våre parsere.

Hvis EmuDeck er installert på SD-kort, leter programmet også etter SRM direkte under for eksempel:

```text
/run/media/deck/FF4Y7/Emulation/tools/
```

## Installer

Pakk ut ZIP-en og åpne `EmuDeck Favorites Sync.desktop` fra mappen.

Hvis Deck spør om filen skal kjøres: velg å kjøre den. Første gang vil kontrollpanelet spørre om programmet skal installeres.

Du kan også installere manuelt fra terminal:

```bash
bash install.sh
```

Hvis du har klonet prosjektet fra GitHub, kan du senere oppdatere fra samme mappe:

```bash
bash update.sh
```

Etter installering ligger kontrollpanelet også her:

```text
~/.local/share/emudeck-favorites-sync/EmuDeck Favorites Sync.desktop
```

## Bruk

Åpne `EmuDeck Favorites Sync.desktop`.

Der får du en enkel meny:

- Skru på automatisk sync
- Skru av automatisk sync
- Status og favoritter
- Oppdater Steam nå
- Velg SRM AppImage
- Importer stagede favoritter til Steam nå
- Lag feilsøkingsrapport

Vanlig bruk er bare:

1. Skru på automatisk sync.
2. Favorittmarker spill i ES-DE.
3. Lukk Steam helt.
4. Programmet syncer og kjører SRM når det får mulighet.
5. Start Steam igjen.

## Hvordan autosync fungerer

- Fra 0.6.11 skal autosync ikke sjekke hele tiden.
- Den riktige triggeren er at ES-DE lukkes. Da kjøres `esde-closed`, favoritter leses én gang, og endringen lagres som pending hvis Steam fortsatt kjører.
- En liten startup-service kan behandle pending ved neste trygge oppstart, men det finnes ingen gjentakende timer i denne modellen.
- Når Steam er lukket, kjører programmet først SRM `remove` på eksisterende favorittsync-parsere.
- Deretter fjerner programmet gamle Steam-shortcuts som ikke lenger finnes i ES-DE favorites.
- Til slutt skriver programmet ny SRM-staging og kjører SRM `add` på dagens favoritter.
- Før programmet sier “ingen endring”, sjekker det også om Steam-biblioteket faktisk mangler ønskede favoritter eller har gamle favoritter liggende.
- `Oppdater Steam nå` tvinger alltid en full kontrollrunde, selv om ES-DE-favorittlisten ikke har endret seg siden sist.
- Fjerning matcher SRM-shortcuts mer tolerant, slik at små forskjeller i anførselstegn og `LaunchOptions` ikke hindrer cleanup.
- Ved oppgradering restarter installasjonen autosync-servicen hvis den allerede kjører, slik at bakgrunnen bruker ny kode.
- Status viser nå også når autosync sist sjekket og om Steam sist ble sett som running eller stopped.
- Mens SRM `add` kjøres, aktiveres bare `ES-DE Favorites Sync`-parserne midlertidig.
- Andre SRM-parsere settes tilbake slik de var etterpå.
- Spillene legges bare i konsollens vanlige SRM-collection, ikke i en ekstra samlet `ES-DE Favorites`-collection.
- Favorittsync-parserne blir liggende i SRM selv om en konsoll akkurat nå har null favoritter; manifestet blir bare tomt.

## Hvis programmet ikke finner Steam ROM Manager

Åpne kontrollpanelet og velg:

```text
Velg SRM AppImage
```

Pek på Steam ROM Manager sin `.AppImage`-fil. Programmet husker valget under:

```text
~/.local/state/emudeck-favorites-sync/srm-app-path.txt
```

Deretter kan du prøve:

```text
Kjør SRM add nå
```

## Sikkerhet

- Programmet skriver ikke mens Steam kjører.
- Programmet lager backup før SRM-konfig og Steam-shortcuts endres.
- Eksisterende EmuDeck-parsere røres ikke.
- Eksisterende Steam-shortcuts bevares.
- Global SRM `nuke` brukes aldri.
- Hvis `deleteDisabledShortcuts` er aktivert i SRM, blokkeres SRM-staging.

## Terminalkommandoer hvis du trenger dem

```bash
~/.local/bin/emudeck-favorites-sync autosync-on
~/.local/bin/emudeck-favorites-sync autosync-status
~/.local/bin/emudeck-favorites-sync autosync-off
~/.local/bin/emudeck-favorites-sync autosync-now
~/.local/bin/emudeck-favorites-sync esde-closed
~/.local/bin/emudeck-favorites-sync srm-remove-now
~/.local/bin/emudeck-favorites-sync srm-add-now
~/.local/bin/emudeck-favorites-sync set-srm-path "/path/to/Steam-ROM-Manager.AppImage"
~/.local/bin/emudeck-favorites-sync steam-import-now
```

## Feilsøking

State, logg og backups ligger under:

```text
~/.local/state/emudeck-favorites-sync
```

Autosync-loggen ligger her:

```text
~/.local/state/emudeck-favorites-sync/autosync.log
```

## Avinstaller

Fra den utpakkede mappen:

```bash
bash uninstall.sh
```
