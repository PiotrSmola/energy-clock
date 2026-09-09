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

## Publikacja

Ceny na dobe nastepna publikowane sa okolo 14:00 dnia poprzedniego.
Przed publikacja API zwraca pusta liste, nie blad.
