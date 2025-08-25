# utils/csv_utils.py
import pandas as pd
import io

def parse_worklog_csv(uploaded_file):
    errors = []
    try:
        content = uploaded_file.read()
        df = pd.read_csv(io.BytesIO(content), sep=";", dtype=str, encoding="utf-8")
    except Exception as e:
        errors.append(f"CSV konnte nicht gelesen werden: {e}")
        return None, errors

    expected = ["Ticketnummer", "Datum", "benötigte Zeit in h", "Uhrzeit", "Beschreibung"]
    missing = [c for c in expected if c not in df.columns]
    if missing:
        errors.append(f"Fehlende Spalten: {', '.join(missing)}")
        return None, errors
    df = df.fillna("")
    return df, errors

def validate_worklog_rows(df: pd.DataFrame):
    errors = []
    for idx, row in df.iterrows():
        if not str(row["Ticketnummer"]).strip():
            errors.append(f"Zeile {idx+1}: Ticketnummer fehlt.")
        try:
            pd.to_datetime(row["Datum"], dayfirst=True)
        except Exception:
            errors.append(f"Zeile {idx+1}: Ungültiges Datum '{row['Datum']}'.")
        try:
            pd.to_datetime(row["Uhrzeit"])
        except Exception:
            errors.append(f"Zeile {idx+1}: Ungültige Uhrzeit '{row['Uhrzeit']}'.")
        s = str(row["benötigte Zeit in h"]).replace(",", ".")
        try:
            hours = float(s)
            if abs((hours*4) - round(hours*4)) > 1e-6:
                errors.append(f"Zeile {idx+1}: Dauer {row['benötigte Zeit in h']}h ist kein 15-Minuten-Vielfaches.")
        except Exception:
            errors.append(f"Zeile {idx+1}: Ungültige Dauer '{row['benötigte Zeit in h']}'.")
    return errors
