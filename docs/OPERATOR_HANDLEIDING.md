# Operatorhandleiding — CSTR Digital Twin (R-101)

Open het dashboard: **http://127.0.0.1:8000** (of het adres dat de beheerder opgeeft).

## Het proces in het kort

Reactor R-101 (1 m³) voert een sterk exotherme reactie uit. De reactor wordt gekoeld via een
mantel/spiraal. Temperatuurregelaar **TIC-101** houdt de reactor op **350 K** door de
koelmiddeltemperatuur **T_c** aan te passen.

> **Belangrijk:** het werkpunt is zonder regelaar *instabiel*. Een kleine temperatuurstijging
> versnelt de reactie, en die produceert dan nog meer warmte. Alleen de regelaar houdt dit in toom.
> Staat TIC-101 in HAND, of valt de koeling weg, dan kan de temperatuur exponentieel oplopen
> (**thermal runaway**).

## Het scherm

| Zone | Wat zie je |
|---|---|
| **Kopbalk** | Verbinding, simulatiestatus, **TIC-101 AUTO/HAND** (oranje = hand of verzadigd), twin-status, tijd `t` in minuten. |
| **Alarmbalk** | Alleen bij afwijkingen: oranje = hoge temperatuur, rood = TRIP. |
| **Procestoestand** | T (met balk tot de trip-grens), C_A, conversie, koelmiddel T_c, koeling Q en reactiewarmte (kW). |
| **Scenario's** | Trainingsknoppen voor realistische storingen (zie hieronder). |
| **Besturing** | Start / Stop / Reset en simulatiesnelheid. |
| **Digital Twin** | Twin aan/uit, blend-factor, status van de datastroom, laatste meting, modelafwijking. |
| **Grafiek T(t)** | Zwart = reactortemperatuur, blauw = koelmiddel T_c, grijs gestippeld = setpoint, paars gestippeld = voorspelling, oranje/rood = waarschuwings-/tripgrens, cyaan punten = metingen. |
| **Grafiek C_A(t)** | Concentratie reactant in de reactor. Hoog en koud = **ophoping** (gevaarlijk). |
| **Warmtebalans** | Rood = warmte die de reactie maakt, blauw = warmte die je kunt afvoeren. Als rood boven blauw ligt, warmt de reactor op. Waar rood steiler loopt dan blauw, versnelt elke graad extra zichzelf. |
| **Parameters** | Tabbladen Proces / Regelaar / Start / Veiligheid / Simulatie. Gewijzigde waarden oranje tot *Toepassen*. |
| **Open-loop evenwichten** | Waar de reactor naartoe loopt als T_c vast zou blijven staan. "nee" = instabiel. |
| **Gebeurtenissen** | Log van starts, wijzigingen, twin-acties en trips. |

Kleurgebruik: grijs = normaal. Kleur betekent altijd dat er iets aandacht vraagt.

## Dagelijkse handelingen

**Een instelling wijzigen (bijv. setpoint of voeding)**
1. Kies het tabblad (*Regelaar* voor het setpoint, *Proces* voor F of C_A,in).
2. Klik **Wat-als** en kies een horizon: de paarse stippellijn toont het verwachte verloop,
   inclusief de reactie van de regelaar. Verschijnt *"Runaway verwacht"*, pas het dan niet toe.
3. Klik **Toepassen** (of *Annuleren*).

**Regelaar naar HAND** (tab *Regelaar* → *TIC-101 in automaat* uit): T_c blijft staan op de
laatste waarde. Blijf de temperatuur actief bewaken; het werkpunt drijft vanzelf weg.
Terug naar AUTO gaat stootvrij.

**Live meekijken met de installatie (twin)**
1. Zet *Digital Twin-modus* aan. De status moet binnen enkele seconden *"Live"* tonen.
   TIC-101 toont dan *"volgt meting"*: het model gebruikt de echte koelmiddeltemperatuur.
2. *Modelafwijking* (ΔT, ΔC_A, ΔQ) laat zien hoe goed het model de installatie volgt.
   Blijft ΔT structureel groter dan enkele kelvin, meld dit dan aan procestechnologie.
3. Als de datastroom zowel T_c als Q bevat, schat de twin de koelcapaciteit UA. Een dalende
   UA (tab *Proces*) wijst op vervuiling of een probleem met de koelwaterpomp.
4. *"Twin: geen data"* betekent dat de datastroom is gestopt. Waarschuw de beheerder.

## Trainingsscenario's

| Knop | Wat gebeurt er | Wat leer je |
|---|---|---|
| Normaal bedrijf | Standaardwaarden, start in het werkpunt | Referentie |
| Koelingsuitval | UA = 0 (koelwaterpomp uit) | T stijgt eerst langzaam, dan steeds sneller (runaway); de regelaar kan niets meer doen |
| Verminderde koeling | UA halveert (vervuiling) | Regelaar raakt verzadigd, excursie tot ~410 K, daarna herstel |
| Regelaar in hand | TIC-101 naar HAND + kleine verstoring | Het werkpunt is instabiel en drijft vanzelf weg |
| Koude opstart met ophoping | Reactor vol verse voeding bij 300 K | Zodra de reactie aanslaat, komt alle opgehoopte energie tegelijk vrij: T > 600 K, trip |

## Alarmen

**Oranje: HOGE TEMPERATUUR (T ≥ 400 K)**
- Controleer de koeling: staat TIC-101 in AUTO? Is T_c al minimaal (verzadigd)? Is UA gedaald?
- Verlaag de voeding (F of C_A,in) om de warmteproductie te beperken.
- Gebruik *Wat-als* om te controleren dat de temperatuur daalt.

**Oranje KPI T_c: REGELAAR VERZADIGD**
- De koeling staat maximaal en de regelaar heeft geen reserve meer. Verlaag direct de voeding.

**Rood: FAILSAFE TRIP (T ≥ 600 K)**
- Het model schakelt noodkoeling in en stopt de reactantvoeding.
- In twin-modus komt de trip van de **gemeten** temperatuur: volg direct de noodprocedure
  van de installatie. De app is een hulpmiddel en vervangt het beveiligingssysteem niet.
- Zoek de oorzaak in het gebeurtenissenlog.
- De resetknop wordt pas actief als T onder 400 K is.

## Reset
*Reset* zet de simulatie terug naar de begintoestand en wist de grafieken. Parameters blijven behouden.
*Standaardwaarden* (onder in het parameterpaneel) zet alle parameters terug.

## Snelle referentie standaardwerkpunt

| Grootheid | Waarde |
|---|---|
| T / setpoint | 350 K |
| C_A | 1,0 mol/L |
| Conversie | 80 % |
| T_c (koelmiddel) | ≈ 316 K (bereik 280–360 K) |
| Reactiewarmte | ≈ 415 kW |
| Koeling (mantel) | ≈ 343 kW |
| Waarschuwing / Trip | 400 K / 600 K |
