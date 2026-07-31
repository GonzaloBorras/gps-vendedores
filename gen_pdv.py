# -*- coding: utf-8 -*-
import json
import pandas as pd

FILES = [
    (r'C:\Users\gborrasar\Downloads\Clientes activos Tucuman 31.07.26.xlsx', 'TUCUMAN'),
    (r'C:\Users\gborrasar\Downloads\Clientes activos Catamarca 31.07.26.xlsx', 'CATAMARCA'),
]
OUT = r'C:\Users\gborrasar\Documents\Default Project\gps-vendedores\pdv.json'


def load(src, prov):
    df = pd.read_excel(src, header=0)
    out = []
    dropped = 0
    for i in range(len(df)):
        try:
            lat = float(df.iat[i, 5])
            lon = float(df.iat[i, 4])
        except (TypeError, ValueError):
            lat = lon = 0.0
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180) or (lat == 0 and lon == 0):
            dropped += 1
            continue
        c = '' if pd.isna(df.iat[i, 0]) else str(int(df.iat[i, 0]))
        def s(idx):
            v = df.iat[i, idx]
            return '' if pd.isna(v) else str(v)
        altura = s(3)
        vta = s(6)
        out.append({
            'c': c,
            'r': s(1),
            'calle': s(2),
            'altura': altura,
            'vta': vta,
            'lat': round(lat, 6),
            'lon': round(lon, 6),
            'prov': prov,
        })
    return out, dropped


pdv = []
for src, prov in FILES:
    rows, dropped = load(src, prov)
    pdv.extend(rows)
    print(prov, '- pdv:', len(rows), '| sin coordenadas:', dropped)

with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(pdv, f, ensure_ascii=False)
print('Total PDV:', len(pdv), '->', OUT)
