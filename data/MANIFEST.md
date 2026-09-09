# Data manifest

Dane NIE sa przechowywane w tym repozytorium.

Zrodlo:   API raportow PSE - https://api.raporty.pse.pl/
Dostep:   publiczny, bez klucza i bez rejestracji

## Zakres

Poczatek danych: 2024-06-14 (start polskiego rynku 15-minutowego)
Rozdzielczosc:   15 minut, 96 interwalow na dobe

UWAGA: w dobe zmiany czasu jest 92 albo 100 interwalow, nie 96.
Zmierzone: 2026-03-29 -> 92, 2025-10-26 -> 100.

## Endpointy

csdac-pln     cena rynku dnia nastepnego (PLN/MWh)
pdgobpkd      plan generacji (PV, wiatr, jednostki konwencjonalne)
his-wlk-cal   wykonanie i zapotrzebowanie

## Jak odtworzyc

    ./make.ps1 backfill

albo bezposrednio w kontenerze:

    docker exec energy-clock-web-1 python manage.py backfill
    docker exec energy-clock-web-1 python manage.py backfill --start 2026-01-01 --end 2026-03-31
    docker exec energy-clock-web-1 python manage.py backfill --endpoint prices

Backfill pomija dni juz kompletne (liczba rekordow rowna liczbie interwalow
wyliczonej dla danej doby), wiec mozna go uruchamiac wielokrotnie.

## Ostatni pelny backfill

Data:            2026-09-09
Zakres:          2024-06-14 .. 2026-09-09 (818 dob)
Ceny:            78 528 rekordow, 818/818 dob kompletnych
Generacja:       78 528 rekordow, 818/818 dob kompletnych
Doby zmiany czasu w zbiorze: 2024-10-27 (100), 2025-03-30 (92),
                             2025-10-26 (100), 2026-03-29 (92)
Interwaly z cena ujemna:     2 985

## Publikacja

Ceny na dobe nastepna publikowane sa okolo 14:00 dnia poprzedniego.
Przed publikacja API zwraca pusta liste, nie blad.
