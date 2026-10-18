import Foundation
import Testing
@testable import VoiceCore

/// What Supertonic-3 is given for a text in `language`: the call's and the voice note's path (`SpeechRequest.spokenInput`).
private func spoken(_ text: String, _ language: String) throws -> String {
    let body = try JSONSerialization.data(withJSONObject: ["input": text, "language": language])
    return try SpeechRequest(json: body).spokenInput
}

/// The 0.9.3 plan's rows per language: what is written, what Supertonic-3 is given. Every planned row is here, the
/// ones that must stay as written too; every row runs through the whole path, so a pass in the wrong order fails.
enum LanguageTable {
    typealias Row = (kind: String, written: String, said: String)

    static let es: [Row] = [
        // planned
        ("times", "a las 9:30", "a las nueve y treinta"),
        ("times", "14:00-15:00", "de catorce a quince"),
        ("dates", "el 16/10/2026", "el dieciséis de octubre de dos mil veintiséis"),
        ("money", "12,50 €", "doce euros con cincuenta"),
        ("ordinals", "la 1.ª vez", "la primera vez"),
        ("temperatures", "-2 °C", "menos dos grados"),
        ("phones", "612 345 678", "seis uno dos, tres cuatro cinco, seis siete ocho"),
        ("chords", "C, G, Am y F", "do, sol, la menor y fa"),
        ("chords", "Fa#m y Sib", "fa sostenido menor y si bemol"),
        ("chords", "Si quieres, mi canción empieza en La.", "Si quieres, mi canción empieza en La."),
        // the plan's rules
        ("times", "a la 1:00 y a las 21:05", "a la una y a las veintiuna y cinco"),
        ("times", "de 9:00 a 13:30", "de nueve a trece y treinta"),
        ("dates", "el 1/2", "el uno de febrero"),
        ("dates", "la mitad: 1/2", "la mitad: uno/dos"),  // no date word: a fraction's numbers
        ("money", "1 €, $21 y 0,50 €", "un euro, veintiún dólares y cincuenta céntimos"),
        ("money", "1.250.000 € y 2.000.000 €", "un millón doscientos cincuenta mil euros y dos millones de euros"),
        ("percent", "4,5 % y 4.5%", "cuatro coma cinco por ciento y cuatro coma cinco por ciento"),
        ("decimals", "3,14159 y 2,05", "tres coma uno cuatro uno cinco nueve y dos coma cero cinco"),
        ("big numbers", "1.250.000 habitantes", "un millón doscientos cincuenta mil habitantes"),
        ("big numbers", "1,250,000", "un millón doscientos cincuenta mil"),
        ("ordinals", "3.er lugar, el 2º", "tercer lugar, el segundo"),
        ("temperatures", "18 °C, 1 °C y 18º C", "dieciocho grados, un grado y dieciocho grados"),
        ("units", "12 km, 1 km y 2,5 kg", "doce kilómetros, un kilómetro y dos coma cinco kilogramos"),
        ("units", "50 km/h y 5-10 km", "cincuenta kilómetros por hora y de cinco a diez kilómetros"),
        ("ranges", "5-10, ganaron 3-1", "de cinco a diez, ganaron tres-uno"),  // a score stays
        ("minus", "COVID-19 y -20 euros", "COVID-diecinueve y menos veinte euros"),
        ("phones", "+34 612 345 678", "más tres cuatro, seis uno dos, tres cuatro cinco, seis siete ocho"),
        ("symbols", "#1 y 2+2", "número uno y dos más dos"),
        ("cardinals", "21 años, 1 y 2", "veintiún años, uno y dos"),
        ("years", "en 1998", "en mil novecientos noventa y ocho"),
        ("chords", "A, B y C", "A, B y C"),  // no suffix: a list
        ("chords", "Am - F - C - G", "la menor, fa, do, sol"),
        ("chords", "C#sus4", "C sostenido sus cuatro"),  // deferred: as written, the sharp said
        // review, 17 Oct
        ("phones", "Llámame al 912345678.", "Llámame al nueve uno dos, tres cuatro cinco, seis siete ocho."),
        ("times", "a las 18:30 h", "a las dieciocho y treinta"),
        ("cardinals", "2 hijos y 1 hija, 1 día y 21 personas", "dos hijos y una hija, un día y veintiuna personas"),
        // review 2, 17 Oct (I1): the hundreds take the noun's gender too, below a million
        ("cardinals", "Tengo 200 páginas y 300 personas.", "Tengo doscientas páginas y trescientas personas."),
        ("cardinals", "Faltan 2.500 personas y 200 libros", "Faltan dos mil quinientas personas y doscientos libros"),
        ("cardinals", "1.200.300 personas", "un millón doscientas mil trescientas personas"),
        // review 2, 17 Oct (I2): "°" typed for "º" after an article or before a month; else degrees
        ("ordinals", "El 1° de mayo llegó 3°.", "El primero de mayo llegó tercero."),
        ("temperatures", "Mañana 30°, en la sierra -2°.", "Mañana treinta grados, en la sierra menos dos grados."),
        // review 2, 17 Oct (I4): spaces as thousands marks (the Spanish Academy allows them); a phone stays 3-3-3
        ("big numbers", "Hay 1 250 000 habitantes.", "Hay un millón doscientos cincuenta mil habitantes."),
        ("money", "Tengo 10 000 euros y 2 500 €.", "Tengo diez mil euros y dos mil quinientos euros."),
        ("big numbers", "12 500 000 y 1 234 567 890", "doce millones quinientos mil y mil doscientos treinta y cuatro millones quinientos sesenta y siete mil ochocientos noventa"),
        // review 2, 17 Oct (I3): a phone word counts at a word's start only, and a year range is no phone
        ("phones", "El automóvil cuesta 345 678 euros.", "El automóvil cuesta trescientos cuarenta y cinco mil seiscientos setenta y ocho euros."),
        ("phones", "Mi móvil se rompió en 2019-2020.", "Mi móvil se rompió en dos mil diecinueve-dos mil veinte."),
        ("phones", "Mi teléfono es el 612 345 678.", "Mi teléfono es el seis uno dos, tres cuatro cinco, seis siete ocho."),
        // review 2, 17 Oct (I5): chords only in a music context; "C#" elsewhere is the programming language
        ("chords", "La cumbre del G7 fue en junio.", "La cumbre del G siete fue en junio."),
        ("chords", "Mi Audi A7 es rojo.", "Mi Audi A siete es rojo."),
        ("chords", "Programo en C# y F#.", "Programo en C sharp y F sharp."),
        ("chords", "Los acordes son C, G7 y Am.", "Los acordes son do, sol séptima y la menor."),
        ("chords", "Toca F#m7 y luego Am.", "Toca fa sostenido menor séptima y luego la menor."),
        ("chords", "Hoy hace sol y la casa es bonita.", "Hoy hace sol y la casa es bonita."),
        ("cardinals", "1 mañana de sol", "una mañana de sol"),  // review 2 (M6): "la mañana" is feminine
    ]

    static let fr: [Row] = [
        // planned; Ruling (the live check, 17 Oct): numbers with spaces, not hyphens ("soixante-dix-huit" came back
        // "60 X8", "soixante dix huit" right)
        ("times", "à 9h30", "à neuf heures trente"),
        ("dates", "le 1/10", "le premier octobre"),
        ("ordinals", "3e, 1re", "troisième, première"),
        ("phones", "06 12 34 56 78", "zéro six, douze, trente quatre, cinquante six, soixante dix huit"),
        ("chords", "Sib et Ré7", "si bémol et ré sept"),
        ("chords", "Si tu veux.", "Si tu veux."),
        // the plan's rules
        ("times", "à 13:00 et à 13h", "à treize heures et à treize heures"),
        ("times", "à 1h et à 21h05", "à une heure et à vingt et une heures cinq"),
        ("times", "14:00-15:00", "de quatorze à quinze heures"),
        ("dates", "le 16/10/2026", "le seize octobre deux mille vingt six"),
        ("dates", "le 1 octobre", "le premier octobre"),
        ("dates", "1/2", "un/deux"),  // no date word: a fraction's numbers
        ("money", "12,50 €", "douze euros cinquante"),
        ("money", "1 € et 0,50 €", "un euro et cinquante centimes"),
        ("money", "2 000 000 €", "deux millions d'euros"),
        ("big numbers", "1 250 000 habitants", "un million deux cent cinquante mille habitants"),
        ("percent", "4,5 %", "quatre virgule cinq pour cent"),
        ("temperatures", "-2 °C et 1,5 °C", "moins deux degrés et un virgule cinq degré"),  // under two: singular
        ("units", "12 km et 2,5 kg", "douze kilomètres et deux virgule cinq kilogrammes"),
        ("ordinals", "1er podium, 2nd et le 3ème", "premier podium, second et le troisième"),
        ("phones", "+33 6 12 34 56 78", "plus trente trois, six, douze, trente quatre, cinquante six, soixante dix huit"),
        ("ranges", "5-10", "de cinq à dix"),
        ("years", "en 1998", "en mille neuf cent quatre vingt dix huit"),
        ("chords", "Am, Dm et E7", "la mineur, ré mineur et mi sept"),
        ("minus", "-5", "moins cinq"),
        // review 2, 17 Oct (I5): chords only in a music context
        ("chords", "Je code en C#, si tu veux.", "Je code en C sharp, si tu veux."),
        ("chords", "Si tu veux, Do, Ré, Mi", "Si tu veux, Do, Ré, Mi"),
        // review 2, 17 Oct (I8): phone numbers written with dots
        ("phones", "06.12.34.56.78", "zéro six, douze, trente quatre, cinquante six, soixante dix huit"),
        ("phones", "Appelle le 01.23.45.67.89.", "Appelle le zéro un, vingt trois, quarante cinq, soixante sept, quatre vingt neuf."),
    ]

    static let it: [Row] = [
        // planned
        ("times", "alle 9:30", "alle nove e trenta"),
        ("money", "12,50 €", "dodici euro e cinquanta"),
        ("ordinals", "arrivato 3º", "arrivato terzo"),
        ("percent", "del 4,5%", "del quattro virgola cinque per cento"),
        ("years", "nel 1998", "nel millenovecentonovantotto"),
        ("chords", "Lam e Re7", "la minore e re settima"),
        ("chords", "E poi la canzone.", "E poi la canzone."),
        // the plan's rules
        ("times", "alle 13:00 e all'1:00", "alle tredici e all'una"),
        ("times", "alle 9.30", "alle nove e trenta"),
        ("times", "14:00-15:00 e 1:00-3:00", "dalle quattordici alle quindici e dall'una alle tre"),
        ("dates", "il 16/10/2026", "il sedici ottobre duemilaventisei"),
        ("dates", "il 1/10 e il 1 ottobre", "il primo ottobre e il primo ottobre"),
        ("money", "1 € e 0,50 €", "un euro e cinquanta centesimi"),
        ("money", "2.000.000 € e $5", "due milioni di euro e cinque dollari"),
        ("big numbers", "1.250.000 abitanti", "un milione duecentocinquantamila abitanti"),
        ("ordinals", "la 1ª volta, l'11º", "la prima volta, l'undicesimo"),
        ("temperatures", "-2 °C e 1 °C", "meno due gradi e un grado"),
        ("units", "12 km e 2,5 kg", "dodici chilometri e due virgola cinque chilogrammi"),
        ("phones", "347 123 4567", "tre quattro sette, uno due tre, quattro cinque sei sette"),
        ("ranges", "5-10", "da cinque a dieci"),
        ("cardinals", "1 libro e 2", "un libro e due"),
        ("chords", "Do, Sol, Lam e Fa", "do, sol, la minore e fa"),
        ("cardinals", "1 casa e 1 problema", "una casa e un problema"),  // review, 17 Oct
        // review 2, 17 Oct (I2): "°" typed for "º" after an article or before a month; else degrees
        ("ordinals", "Il 1° maggio è festa.", "Il primo maggio è festa."),
        ("ordinals", "È arrivato 3° alla gara, la 2° volta, l'11° posto.", "È arrivato terzo alla gara, la seconda volta, l'undicesimo posto."),
        ("temperatures", "Oggi 25°, di notte -2°.", "Oggi venticinque gradi, di notte meno due gradi."),
        ("big numbers", "10 000 abitanti", "diecimila abitanti"),  // review 2 (I4)
        // review 2, 17 Oct (I5): chords only in a music context
        ("chords", "La Sim del telefono", "La Sim del telefono"),
        ("chords", "Gli accordi: La, Mi e Sim.", "Gli accordi: la, mi e si minore."),
        ("chords", "Do una mano a Re Artù.", "Do una mano a Re Artù."),
    ]

    static let pt: [Row] = [
        // planned (pt_PT)
        ("times", "às 21:00", "às vinte e uma horas"),
        ("dates", "em 16/10/2026", "em dezasseis de outubro de dois mil e vinte e seis"),
        ("money", "12,50 €", "doze euros e cinquenta cêntimos"),
        ("big numbers", "1.000.000.000", "mil milhões"),
        ("chords", "F#m e Bb", "fá sustenido menor e si bemol"),
        ("chords", "Sol e mar.", "Sol e mar."),
        // the plan's rules
        ("times", "às 13h e às 9h30", "às treze horas e às nove horas e trinta"),
        ("times", "à 1:00", "à uma hora"),
        ("times", "14:00-15:00", "das catorze às quinze horas"),
        ("dates", "em 1/10 e 1 de outubro", "em primeiro de outubro e primeiro de outubro"),
        ("money", "1 € e 0,01 €", "um euro e um cêntimo"),
        ("money", "2.000.000 €", "dois milhões de euros"),
        ("ordinals", "a 1ª vez, em 3º lugar, o 21.º", "a primeira vez, em terceiro lugar, o vigésimo primeiro"),
        ("temperatures", "-2 °C e 18º C", "menos dois graus e dezoito graus"),
        ("units", "12 km e 2 kg", "doze quilómetros e dois quilogramas"),
        ("percent", "4,5%", "quatro vírgula cinco por cento"),
        ("phones", "912 345 678", "nove um dois, três quatro cinco, seis sete oito"),
        ("ranges", "5-10", "de cinco a dez"),
        ("chords", "Em 2026, Sim.", "Em dois mil e vinte e seis, Sim."),  // words that look like chords
        ("cardinals", "1 filho e 2 filhas", "um filho e duas filhas"),  // review, 17 Oct
        // review 2, 17 Oct (I1): the hundreds and "dois mil" take the noun's gender too, below a million
        ("cardinals", "Tenho 200 pessoas e 2.000 casas", "Tenho duzentas pessoas e duas mil casas"),
        ("cardinals", "1.000.200 pessoas e 200 livros", "um milhão e duzentas pessoas e duzentos livros"),
        // review 2, 17 Oct (I2): "°" typed for "º" after an article or before a month; else degrees
        ("ordinals", "No 1° de maio ficou 3°.", "No primeiro de maio ficou terceiro."),
        ("temperatures", "Hoje 25°.", "Hoje vinte e cinco graus."),
        ("big numbers", "10 000 pessoas", "dez mil pessoas"),  // review 2 (I4)
    ]

    static let nl: [Row] = [
        // planned
        ("times", "om 9.30 uur", "om negen uur dertig"),
        ("times", "14:00-15:00", "van veertien tot vijftien uur"),
        ("dates", "op 16-10-2026", "op zestien oktober tweeduizend zesentwintig"),
        ("ordinals", "zijn 1e podium", "zijn eerste podium"),
        ("years", "in 1998", "in negentienhonderdachtennegentig"),
        ("chords", "F#m en D7", "Fis mineur en D zeven"),
        ("years", "1998 mensen", "duizendnegenhonderdachtennegentig mensen"),  // no year: a cardinal
        // the plan's rules
        ("times", "om 13:00 en om 1:00", "om dertien uur en om één uur"),
        ("times", "om 9:30 uur", "om negen uur dertig"),
        ("times", "het kost 9.30", "het kost negen komma dertig"),  // no time word: a decimal
        ("dates", "op 1/10", "op één oktober"),
        ("dates", "16 oktober 2026", "zestien oktober tweeduizend zesentwintig"),
        ("money", "€ 12,50", "twaalf euro vijftig"),
        ("money", "1 € en 0,50 €", "één euro en vijftig cent"),
        ("money", "2.000.000 €", "twee miljoen euro"),
        ("big numbers", "1.250.000 inwoners", "een miljoen tweehonderdvijftigduizend inwoners"),
        ("percent", "4,5%", "vier komma vijf procent"),
        ("temperatures", "-2 °C en 1 °C", "min twee graden en één graad"),
        ("units", "12 km en 2,5 kg", "twaalf kilometer en twee komma vijf kilogram"),
        ("ordinals", "de 3e, 8ste en 21ste", "de derde, achtste en eenentwintigste"),
        ("phones", "06-12345678", "nul zes, één twee drie vier vijf zes zeven acht"),
        ("ranges", "5-10", "van vijf tot tien"),
        ("years", "sinds 2026 en in 2000", "sinds tweeduizend zesentwintig en in tweeduizend"),
        ("years", "in 1900 (en 1066)", "in negentienhonderd (en duizendzesenzestig)"),
        ("chords", "Bb, Eb en Ab7", "Bes, Es en As zeven"),
        ("chords", "Am, C en G", "A mineur, C en G"),
        ("big numbers", "1 250 000 inwoners", "een miljoen tweehonderdvijftigduizend inwoners"),  // review 2 (I4)
        ("chords", "Bes en Fis", "Bes en Fis"),  // review 2 (I5): names without a suffix stay
        ("chords", "Ik programmeer in C#.", "Ik programmeer in C sharp."),
        ("times", "tussen 14:00-15:00", "tussen veertien en vijftien uur"),  // review 2 (M2): tussen ... en
    ]

    static let de: [Row] = [
        // planned
        ("times", "um 13:00 Uhr", "um dreizehn Uhr"),
        ("times", "von 14:00-15:00 Uhr", "von vierzehn bis fünfzehn Uhr"),
        ("dates", "der 16.10.2026", "der sechzehnte Oktober zweitausendsechsundzwanzig"),
        ("ordinals", "den 3. November", "den dritten November"),
        ("ordinals", "dein 21. Geburtstag", "dein einundzwanzigster Geburtstag"),
        ("phones", "+49 30 1234567", "plus vier neun, drei null, eins zwei drei vier fünf sechs sieben"),
        ("years", "1987 gebaut", "neunzehnhundertsiebenundachtzig gebaut"),
        ("chords", "Hm und C#m", "H-Moll und Cis-Moll"),
        ("chords", "Bb und B7", "B und H sieben"),
        ("chords", "Am Montag ist es kalt.", "Am Montag ist es kalt."),
        // the plan's rules
        ("times", "um 9:30 und um 1:15", "um neun Uhr dreißig und um ein Uhr fünfzehn"),
        ("times", "um 9.30", "um neun Uhr dreißig"),
        ("dates", "am 16.10.2026", "am sechzehnten Oktober zweitausendsechsundzwanzig"),
        ("dates", "Freitag, 16.10.2026", "Freitag, sechzehnter Oktober zweitausendsechsundzwanzig"),
        ("dates", "am 3.11. um 9:00 Uhr", "am dritten November um neun Uhr"),
        ("dates", "bis zum 1.12.1999", "bis zum ersten Dezember neunzehnhundertneunundneunzig"),
        ("ordinals", "Kapitel 3. Dann", "Kapitel drei. Dann"),  // a sentence's end: no ordinal
        ("ordinals", "Er wurde 3.", "Er wurde drei."),
        ("money", "12,50 €", "zwölf Euro fünfzig"),
        ("money", "1 € und 0,50 €", "ein Euro und fünfzig Cent"),
        ("money", "2.000.000 €", "zwei Millionen Euro"),
        ("big numbers", "1.234.567 Einwohner", "eine Million zweihundertvierunddreißigtausendfünfhundertsiebenundsechzig Einwohner"),
        ("percent", "87,5 %", "siebenundachtzig Komma fünf Prozent"),
        ("temperatures", "18 °C, nachts -3 °C", "achtzehn Grad, nachts minus drei Grad"),
        ("units", "584 km und 2,5 kg, 1 km", "fünfhundertvierundachtzig Kilometer und zwei Komma fünf Kilogramm, ein Kilometer"),
        ("years", "1987 Einwohner", "eintausendneunhundertsiebenundachtzig Einwohner"),  // a noun after it: a number
        ("years", "seit 1987 Mitglied, im Jahr 2026", "seit neunzehnhundertsiebenundachtzig Mitglied, im Jahr zweitausendsechsundzwanzig"),
        ("cardinals", "1 Kind und 1 und 2", "ein Kind und eins und zwei"),
        ("ranges", "5-10", "fünf bis zehn"),
        ("phones", "0170 1234567", "null eins sieben null, eins zwei drei vier fünf sechs sieben"),
        ("chords", "Fis7 und Es", "Fis sieben und Es"),
        ("chords", "Am, Dm und E7", "A-Moll, D-Moll und E sieben"),
        ("chords", "Ab morgen", "Ab morgen"),
        ("cardinals", "1 Stunde und 1 Tag", "eine Stunde und ein Tag"),  // review, 17 Oct
        // the live audit, 17 Oct: German names anywhere in the text keep B as B
        ("chords", "Die Akkorde sind Hm, Fis7, Es-Dur, B und C#m.", "Die Akkorde sind H-Moll, Fis sieben, Es-Dur, B und Cis-Moll."),
        // review 2, 17 Oct (I4): spaces as thousands marks (the German style guides allow them)
        ("big numbers", "Die Stadt hat 1 250 000 Einwohner.", "Die Stadt hat eine Million zweihundertfünfzigtausend Einwohner."),
        // review 2, 17 Oct (I3): "mobil" inside Immobilie and Automobil is no phone word
        ("phones", "Die Immobilie kostet 350 500 Euro.", "Die Immobilie kostet dreihundertfünfzigtausendfünfhundert Euro."),
        ("phones", "Der Automobilclub hat 345 678 Mitglieder.", "Der Automobilclub hat dreihundertfünfundvierzigtausendsechshundertachtundsiebzig Mitglieder."),
        ("phones", "Meine Handynummer: 0170 1234567", "Meine Handynummer: null eins sieben null, eins zwei drei vier fünf sechs sieben"),
        // review 2, 17 Oct (I5): chords only in a music context
        ("chords", "Programmiere in C#.", "Programmiere in C sharp."),
        ("chords", "Plan B und C#m", "Plan B und Cis-Moll"),
        ("chords", "Hm, das weiß ich nicht.", "Hm, das weiß ich nicht."),
        ("chords", "Des Weiteren, Eis und Bis morgen", "Des Weiteren, Eis und Bis morgen"),
        // review 2, 17 Oct (I6): the first of a pair is an ordinal too, and both take the ending of the word before
        ("ordinals", "am 1. und 2. Mai", "am ersten und zweiten Mai"),
        ("ordinals", "vom 1. bis 3. Oktober", "vom ersten bis dritten Oktober"),
        ("ordinals", "der 1. oder 2. Platz", "der erste oder zweite Platz"),
        // review 2 (M7): a decade keeps its ending glued
        ("cardinals", "die 20er Jahre, die 80er-Jahre und die 1920er", "die zwanziger Jahre, die achtziger-Jahre und die neunzehnhundertzwanziger"),
    ]

    static let pl: [Row] = [
        // planned
        ("times", "o 13:00", "o trzynastej"),
        ("times", "przed 9:00", "przed dziewiątą"),
        ("times", "14:00-15:00", "od czternastej do piętnastej"),
        ("dates", "jest piątek, 16.10.2026", "jest piątek, szesnasty października dwa tysiące dwudziestego szóstego roku"),
        ("dates", "Wizyta 3 listopada", "Wizyta trzeciego listopada"),
        ("money", "5 zł", "pięć złotych"),
        ("temperatures", "-3°C", "minus trzy stopnie"),
        ("years", "w 1987 roku", "w tysiąc dziewięćset osiemdziesiątym siódmym roku"),
        ("chords", "H7 i C#m", "H siedem i cis-moll"),
        ("chords", "a-moll, Fis, Es-dur", "a-moll, Fis, Es-dur"),
        // the plan's rules
        ("times", "o 9:30 i o 21:05", "o dziewiątej trzydzieści i o dwudziestej pierwszej zero pięć"),
        ("times", "jest 13:00, o 9.30", "jest trzynasta, o dziewiątej trzydzieści"),
        ("dates", "Spotkanie 16.10.2026", "Spotkanie szesnastego października dwa tysiące dwudziestego szóstego roku"),
        ("dates", "16 października 2026 r.", "szesnastego października dwa tysiące dwudziestego szóstego roku"),
        ("dates", "dnia 3/11", "dnia trzeciego listopada"),
        ("dates", "Dzisiaj jest 1 maja", "Dzisiaj jest pierwszy maja"),
        ("money", "12,50 zł", "dwanaście złotych pięćdziesiąt groszy"),
        ("money", "1 zł, 2 zł i 22 zł", "jeden złoty, dwa złote i dwadzieścia dwa złote"),
        ("money", "12,50 € i 1 €", "dwanaście euro pięćdziesiąt i jedno euro"),
        ("big numbers", "1 234 567 mieszkańców", "milion dwieście trzydzieści cztery tysiące pięćset sześćdziesiąt siedem mieszkańców"),
        ("percent", "87,5%", "osiemdziesiąt siedem przecinek pięć procent"),
        ("temperatures", "18°C, 1°C i 5°C", "osiemnaście stopni, jeden stopień i pięć stopni"),
        ("units", "295 km, 2,5 kg i 22 km",
         "dwieście dziewięćdziesiąt pięć kilometrów, dwa przecinek pięć kilograma i dwadzieścia dwa kilometry"),
        ("units", "5-10 km", "od pięciu do dziesięciu kilometrów"),
        ("years", "od 2000 roku", "od dwutysięcznego roku"),
        ("years", "to był 1987 rok", "to był tysiąc dziewięćset osiemdziesiąty siódmy rok"),
        ("phones", "+48 12 345 67 89", "plus cztery osiem, jeden dwa, trzy cztery pięć, sześć siedem, osiem dziewięć"),
        ("ranges", "5-10", "od pięciu do dziesięciu"),
        ("chords", "Am, Dm i E7", "a-moll, d-moll i E siedem"),
        // the live audit, 17 Oct: an event's date is genitive after "jest" too
        ("dates", "Wizyta u dentysty jest 3 listopada.", "Wizyta u dentysty jest trzeciego listopada."),
        ("dates", "Jutro jest 3 listopada, wtorek, 16.10.2026", "Jutro jest trzeci listopada, wtorek, szesnasty października dwa tysiące dwudziestego szóstego roku"),
        // review 2, 17 Oct (C1): 1 to 2 digits before "rok" are a count, before "roku" a year
        ("years", "Umowa na 1 rok, 1 rok temu", "Umowa na jeden rok, jeden rok temu"),
        ("years", "w 21 roku", "w dwudziestym pierwszym roku"),
        ("chords", "Hm, a-moll", "Hm, a-moll"),  // review 2 (I5)
        // review 2, 17 Oct (I7): a range's first day and a number after od, do or około take the genitive
        ("dates", "od 1 do 3 listopada", "od pierwszego do trzeciego listopada"),
        ("dates", "1-3 listopada", "od pierwszego do trzeciego listopada"),
        ("units", "od 5 do 10 km", "od pięciu do dziesięciu kilometrów"),
        ("units", "do 21 km, około 3 km i do 1 km", "do dwudziestu jeden kilometrów, około trzech kilometrów i do jednego kilometra"),
        ("cardinals", "od 5 lat", "od pięciu lat"),
        ("times", "między 13:00 a 14:00", "między trzynastą a czternastą"),  // review 2 (M3)
    ]

    static let ru: [Row] = [
        // planned
        ("times", "в 13:00", "в тринадцать ноль-ноль"),
        ("times", "14:00-15:00", "с четырнадцати до пятнадцати"),
        ("dates", "Сегодня пятница, 16.10.2026", "Сегодня пятница, шестнадцатое октября две тысячи двадцать шестого года"),
        ("dates", "Приём 3 ноября", "Приём третьего ноября"),
        ("money", "12,50 €", "двенадцать евро пятьдесят центов"),
        ("temperatures", "-3 °C", "минус три градуса"),
        ("phones", "+7 495 123-45-67", "плюс семь, четыреста девяносто пять, сто двадцать три, сорок пять, шестьдесят семь"),
        ("years", "в 1987 году", "в тысяча девятьсот восемьдесят седьмом году"),
        ("ordinals", "21-й день", "двадцать первый день"),
        ("chords", "Am, H7 и Bb", "ля минор, си семь и си-бемоль"),
        // the plan's rules
        ("times", "в 9:30 и в 9:05, в 9.30", "в девять тридцать и в девять ноль пять, в девять тридцать"),
        ("times", "с 9:00 до 13:30", "с девяти до тринадцати тридцати"),
        ("dates", "Встреча 16.10.2026 года", "Встреча шестнадцатого октября две тысячи двадцать шестого года"),
        ("dates", "Завтра 1 мая", "Завтра первое мая"),
        ("dates", "3 ноября 2026 г.", "третьего ноября две тысячи двадцать шестого года"),
        ("money", "12,50 ₽ и 1 ₽", "двенадцать рублей пятьдесят копеек и один рубль"),
        ("money", "$5 и 0,50 €", "пять долларов и пятьдесят центов"),
        ("big numbers", "1 234 567 жителей", "один миллион двести тридцать четыре тысячи пятьсот шестьдесят семь жителей"),
        ("percent", "87,5%, 5% и 21%", "восемьдесят семь целых пять десятых процента, пять процентов и двадцать один процент"),
        ("decimals", "0,5 и 2,5", "ноль целых пять десятых и две целых пять десятых"),
        ("temperatures", "+18 °C и 21 °C", "плюс восемнадцать градусов и двадцать один градус"),
        ("units", "712 км и 2,5 кг", "семьсот двенадцать километров и две целых пять десятых килограмма"),
        ("units", "5 km и 60 км/ч", "пять километров и шестьдесят километров в час"),
        ("ordinals", "1-я и 3-е, в 21-м веке", "первая и третье, в двадцать первом веке"),
        ("years", "с 2000 года и 1987 год", "с двухтысячного года и тысяча девятьсот восемьдесят седьмой год"),
        ("ranges", "5-10", "от пяти до десяти"),
        ("phones", "Телефон: 8 495 123-45-67", "Телефон: восемь, четыреста девяносто пять, сто двадцать три, сорок пять, шестьдесят семь"),
        ("chords", "Am, Dm и E7", "ля минор, ре минор и ми семь"),
        ("chords", "C, F#m и B7", "до, фа-диез минор и си семь"),  // no H in the run: B is си
        ("ordinals", "в 1990-х и 1990-е", "в тысяча девятьсот девяностых и тысяча девятьсот девяностые"),  // review, 17 Oct
        // review 2, 17 Oct (C1): 1 to 2 digits before "год" or "года" are an age or a span, before "году" a year
        ("years", "Мне 2 года, ему 21 год.", "Мне два года, ему двадцать один год."),
        ("years", "3 года назад, 22 года", "три года назад, двадцать два года"),
        ("years", "Ему 1 год и 3 месяца.", "Ему один год и три месяца."),
        ("years", "в 2 года он пошёл", "в два года он пошёл"),
        ("years", "в 21 году", "в двадцать первом году"),
        ("chords", "Пишу на C# и F#.", "Пишу на C sharp и F sharp."),  // review 2 (I5)
        // review 2, 17 Oct (I7): a range's first day and a number after от, до or около take the genitive; по the
        // accusative
        ("dates", "С 5 по 10 мая", "С пятого по десятое мая"),
        ("dates", "с 5 до 10 мая и 5-10 мая", "с пятого до десятого мая и с пятого по десятое мая"),
        ("units", "от 5 до 10 км", "от пяти до десяти километров"),
        ("units", "до 21 км, около 3 км и до 5%", "до двадцати одного километра, около трёх километров и до пяти процентов"),
        ("cardinals", "после 5 лет", "после пяти лет"),
        ("dates", "на 3 ноября и к 3 ноября", "на третье ноября и к третьему ноября"),  // review 2 (M3)
        // review 2 (M8): an end of two to four before a noun keeps the nominative, which the noun's form fits
        ("ranges", "2-3 дня, 1-2 дня и 3-5 дней", "два-три дня, один-два дня и от трёх до пяти дней"),
    ]

    static let ja: [Row] = [
        // planned
        ("times", "9:30に", "9時30分に"),
        ("times", "14:00-15:00です", "14時から15時までです"),
        // Ruling (17 Oct listen): "1234567人" came back "1020円45億627人", "123万4567人" right; 万 and 億 it is
        ("big numbers", "1,234,567人", "123万4567人"),
        ("temperatures", "-3℃", "マイナス3度"),
        ("phones", "03-1234-5678", "ゼロサンの イチニーサンヨンの ゴーロクナナハチ"),
        ("chords", "F#m7、B♭", "エフシャープマイナーセブン、ビーフラット"),
        ("dates", "2026年10月16日", "2026年10月16日"),
        // the plan's rules; 4時 as よじ (the listen: "4時" came back "表示", "よじ" right)
        ("times", "4:00に起きる", "よじに起きる"),
        ("times", "4:15と14:00", "よじ15分と14時"),
        ("dates", "2026/10/16", "2026年10月16日"),
        ("money", "€12.50と$5と¥100", "12.50ユーロと5ドルと100円"),
        // review 2 (M11): "87.5%" came back "87.5センツ", "87.5パーセント" right
        ("percent", "87.5%", "87.5パーセント"),
        ("temperatures", "最低気温は-3℃、最高は18°C", "最低気温はマイナス3度、最高は18度"),
        ("units", "584km、2.5kg", "584キロメートル、2.5キログラム"),
        ("units", "50km/h", "時速50キロメートル"),
        ("phones", "電話は090 1234 5678", "電話はゼロキューゼロの イチニーサンヨンの ゴーロクナナハチ"),
        ("phones", "+81 3 1234 5678", "プラス ハチイチの サンの イチニーサンヨンの ゴーロクナナハチ"),
        ("big numbers", "10000円", "1万円"),
        ("cardinals", "3人と25人", "3人と25人"),
        ("chords", "コードはAm、C#7です", "コードはエーマイナー、シーシャープセブンです"),
        ("chords", "Aランク", "Aランク"),
        ("chords", "第1位、A案とB案", "第1位、A案とB案"),  // review 2: ordinary text stays
        ("big numbers", "注文番号12345678と10000円", "注文番号12345678と1万円"),  // review 2 (M9): an ID keeps its digits
    ]

    static let ko: [Row] = [
        // planned
        ("times", "13:00에", "오후 한 시에"),
        ("times", "9:30에", "오전 아홉 시 30분에"),
        ("times", "14:00-15:00입니다", "오후 두 시부터 세 시까지입니다"),
        ("temperatures", "-3°C", "영하 3도"),
        ("phones", "010-1234-5678", "공일공에 일이삼사에 오육칠팔"),
        ("money", "12.50유로", "12유로 50센트"),
        ("chords", "Am, F#m7", "에이 마이너, 에프 샵 마이너 세븐"),
        ("big numbers", "1,234,567명", "1,234,567명"),
        // the plan's rules
        ("times", "0:30과 12:00", "오전 열두 시 30분과 오후 열두 시"),
        ("times", "11:00-13:00", "오전 열한 시부터 오후 한 시까지"),
        ("dates", "2026년 10월 16일과 6월", "2026년 시월 16일과 유월"),
        ("dates", "2026/6/1", "2026년 유월 1일"),
        ("money", "€12.50, $5.99, ₩1,000", "12유로 50센트, 5달러 99센트, 1000원"),
        ("temperatures", "내일은 18°C, 밤에는 -3°C입니다", "내일은 18도, 밤에는 영하 3도입니다"),
        ("units", "325km이고, 2.5kg입니다", "325킬로미터이고, 2.5킬로그램입니다"),
        ("units", "50km/h", "시속 50킬로미터"),
        ("phones", "+82 10 1234 5678", "플러스 팔이에 일공에 일이삼사에 오육칠팔"),
        ("cardinals", "-5와 +3", "마이너스 5와 플러스 3"),
        ("percent", "87.5%", "87.5%"),
        ("chords", "C, G, Am", "씨, 지, 에이 마이너"),
        ("chords", "A형", "A형"),
        ("chords", "A형, 6개월, 3원칙", "A형, 6개월, 3원칙"),  // review 2: ordinary text stays
    ]

    /// Every language's rows, for the checks every language shares.
    static let all: [(language: String, row: Row)] = [("es", es), ("fr", fr), ("it", it), ("pt", pt), ("nl", nl), ("de", de),
                                                      ("pl", pl), ("ru", ru), ("ja", ja), ("ko", ko)].flatMap { language, rows in rows.map { (language, $0) } }
}

@Suite struct LanguagePronunciation {
    @Test(arguments: LanguageTable.es)
    func spanish(row: LanguageTable.Row) throws {
        #expect(try spoken(row.written, "es") == row.said, "\(row.kind): \(row.written)")
    }

    @Test(arguments: LanguageTable.fr)
    func french(row: LanguageTable.Row) throws {
        #expect(try spoken(row.written, "fr") == row.said, "\(row.kind): \(row.written)")
    }

    @Test(arguments: LanguageTable.it)
    func italian(row: LanguageTable.Row) throws {
        #expect(try spoken(row.written, "it") == row.said, "\(row.kind): \(row.written)")
    }

    @Test(arguments: LanguageTable.pt)
    func portuguese(row: LanguageTable.Row) throws {
        #expect(try spoken(row.written, "pt") == row.said, "\(row.kind): \(row.written)")
    }

    @Test(arguments: LanguageTable.nl)
    func dutch(row: LanguageTable.Row) throws {
        #expect(try spoken(row.written, "nl") == row.said, "\(row.kind): \(row.written)")
    }

    @Test(arguments: LanguageTable.de)
    func german(row: LanguageTable.Row) throws {
        #expect(try spoken(row.written, "de") == row.said, "\(row.kind): \(row.written)")
    }

    @Test(arguments: LanguageTable.pl)
    func polish(row: LanguageTable.Row) throws {
        #expect(try spoken(row.written, "pl") == row.said, "\(row.kind): \(row.written)")
    }

    @Test(arguments: LanguageTable.ru)
    func russian(row: LanguageTable.Row) throws {
        #expect(try spoken(row.written, "ru") == row.said, "\(row.kind): \(row.written)")
    }

    @Test(arguments: LanguageTable.ja)
    func japanese(row: LanguageTable.Row) throws {
        #expect(try spoken(row.written, "ja") == row.said, "\(row.kind): \(row.written)")
    }

    @Test(arguments: LanguageTable.ko)
    func korean(row: LanguageTable.Row) throws {
        #expect(try spoken(row.written, "ko") == row.said, "\(row.kind): \(row.written)")
    }

    /// What FluidAudio breaks never reaches it from a handled token: "#", "º", "ª", or a comma inside a number (ko keeps
    /// its grouped numbers: the audit heard them right).
    @Test func nothingFluidAudioBreaksIsLeft() throws {
        for (language, row) in LanguageTable.all {
            let said = try spoken(row.written, language)
            #expect(!said.contains("#") && !said.contains("º") && !said.contains("ª"), "\(language): \(row.written)")
            if language != "ko" {
                #expect(said.range(of: "[0-9],[0-9]", options: .regularExpression) == nil, "\(language): \(row.written)")
            }
        }
    }

    /// A large number is Foundation's own text in the language's locale, soft hyphens out (pt's billions aside; fr
    /// with spaces for its hyphens).
    @Test(arguments: [("es", "1.234.567"), ("es", "21.000.000"), ("es", "1 234 567"), ("de", "21 000 000"), ("it", "1\u{00A0}234\u{00A0}567"), ("fr", "1 234 567"), ("fr", "21\u{202F}000\u{202F}000"), ("it", "1.234.567"), ("pt", "1.234.567"), ("nl", "1.234.567"), ("de", "21.000.000"), ("pl", "1\u{00A0}234\u{00A0}567"), ("ru", "21 000 000")])
    func largeNumbersAreFoundationsText(language: String, written: String) throws {
        let formatter = NumberFormatter()
        formatter.locale = Locale(identifier: Cardinals.locales[language]!)
        formatter.numberStyle = .spellOut
        let digits = written.filter(\.isNumber)
        var expected = formatter.string(from: NSDecimalNumber(string: digits))!.replacingOccurrences(of: "\u{00AD}", with: "")
        if language == "fr" { expected = expected.replacingOccurrences(of: "-", with: " ") }  // fr: spaces, the live check
        #expect(try spoken(written, language) == expected)
    }
}

/// pl and ru unit words are a hand table; Foundation's MeasurementFormatter (.long) agrees for one, few, many and a
/// fraction.
@Suite struct UnitWords {
    @Test(arguments: ["pl", "ru"])
    func theHandTableMatchesMeasurementFormatter(language: String) throws {
        let formatter = MeasurementFormatter()
        formatter.locale = Locale(identifier: Cardinals.locales[language]!)
        formatter.unitStyle = .long
        formatter.unitOptions = .providedUnit
        let units: [(MeasureUnit, Dimension)] = [
            (.kilometer, UnitLength.kilometers), (.meter, UnitLength.meters), (.centimeter, UnitLength.centimeters),
            (.kilogram, UnitMass.kilograms), (.gram, UnitMass.grams), (.liter, UnitVolume.liters),
            (.kilometerPerHour, UnitSpeed.kilometersPerHour),
        ]
        let pack = try #require(SpokenLanguage.packs.first { $0.code == language })
        for (unit, dimension) in units {
            for (value, x) in [(1.0, Num("1")), (2.0, Num("2")), (5.0, Num("5")), (22.0, Num("22")), (2.5, Num("2", "5"))] {
                let foundation = formatter.string(from: Measurement(value: value, unit: dimension))
                let written = foundation.drop { !$0.isLetter }
                #expect(pack.unit("", unit, pack.plural(x)).dropFirst() == written, "\(language) \(value) \(unit)")
            }
        }
    }
}

@Suite struct PassOrder {
    /// The plan's run order: chords, times, dates, phones, amounts, years, ordinals, ranges, then the numbers left.
    @Test func thePassesRunInThePlansOrder() {
        #expect(SpokenLanguage.order("es") == [
            "chords", "times", "dates", "phones", "money, percent, temperatures and units", "ordinals", "ranges",
            "cardinals and decimals",
        ])
        #expect(SpokenLanguage.order("fr") == SpokenLanguage.order("es"))
        #expect(SpokenLanguage.order("it") == SpokenLanguage.order("es"))
        #expect(SpokenLanguage.order("pt") == SpokenLanguage.order("es"))
        #expect(SpokenLanguage.order("nl") == [
            "chords", "times", "dates", "phones", "money, percent, temperatures and units", "years", "ordinals", "ranges",
            "cardinals and decimals",
        ])
        #expect(SpokenLanguage.order("de") == SpokenLanguage.order("nl"))
        #expect(SpokenLanguage.order("pl") == [
            "chords", "times", "dates", "phones", "money, percent, temperatures and units", "years", "ranges",
            "cardinals and decimals",
        ])
        #expect(SpokenLanguage.order("ru") == SpokenLanguage.order("nl"))
        #expect(SpokenLanguage.order("ja") == [
            "chords", "times", "dates", "phones", "money, percent, temperatures and units", "signs and group commas",
        ])
        #expect(SpokenLanguage.order("ko") == [
            "chords", "times", "dates", "phones", "money, percent, temperatures and units", "signs",
        ])
        #expect(SpokenLanguage.order("en").isEmpty)
    }
}
