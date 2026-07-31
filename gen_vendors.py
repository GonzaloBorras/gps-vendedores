# -*- coding: utf-8 -*-
import json
import unicodedata
import pandas as pd

FILES = [
    (r'C:\Users\gborrasar\Downloads\Clientes activos Tucuman 31.07.26.xlsx', 'TUCUMAN'),
    (r'C:\Users\gborrasar\Downloads\Clientes activos Catamarca 31.07.26.xlsx', 'CATAMARCA'),
]
OUT = r'C:\Users\gborrasar\Documents\Default Project\gps-vendedores\vendors.json'

TAB20 = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b',
         '#e377c2', '#7f7f7f', '#bcbd22', '#17becf', '#aec7e8', '#ffbb78',
         '#98df8a', '#ff9896', '#c5b0d5', '#c49c94', '#f7b6d2', '#c7c7c7',
         '#dbdb8d', '#9edae5', '#393b79', '#f28e2b', '#76b7b2', '#b5cf6b']


def norm(s):
    s = ''.join(c for c in unicodedata.normalize('NFD', str(s))
                if unicodedata.category(c) != 'Mn')
    return ''.join(c for c in s.upper() if c.isalnum())


def vendor_of(ruta):
    r = str(ruta).strip()
    if r.startswith('Ruta '):
        r = r[5:].strip()
    return r.split(' ')[0].upper()


pairs = set()
for src, prov in FILES:
    df = pd.read_excel(src, header=0)
    df.columns = ['Cliente', 'Razon', 'Calle', 'Altura', 'Lon', 'Lat', 'Ruta']
    for ruta in df['Ruta'].dropna().unique():
        pairs.add((prov, vendor_of(ruta)))

pairs = sorted(pairs)

EXTRA_USERS = [
    ('VENDEDORES', 'Facundo Corbalan', 'CORBALAN'),
    ('VENDEDORES', 'Gonzalo Aguilar', 'AGUILAR'),
    ('VENDEDORES', 'Carlos Lazo', 'LAZO'),
    ('VENDEDORES', 'Matias Abib', 'ABIB'),
    ('VENDEDORES', 'Matias Emeterio', 'EMETERIO'),
    ('VENDEDORES', 'Santiago Vera', 'VERA'),
    ('VENDEDORES', 'Leonardo Silva', 'SILVA'),
    ('VENDEDORES', 'Augusto Madrid', 'MADRID'),
    ('VENDEDORES', 'Julian Albonoz', 'ALBONOZ'),
    ('VENDEDORES', 'Saul David', 'DAVID'),
]

vendors = []
for i, (prov, name) in enumerate(pairs):
    slug = norm(name)
    vendors.append({
        'name': name,
        'prov': prov,
        'code': '%s-%02d' % (slug, i + 1),
        'color': TAB20[i % len(TAB20)],
    })

n = len(vendors)
for i, (prov, name, slug) in enumerate(EXTRA_USERS):
    vendors.append({
        'name': name,
        'prov': prov,
        'code': '%s-%02d' % (slug, n + i + 1),
        'color': TAB20[(n + i) % len(TAB20)],
    })

with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(vendors, f, ensure_ascii=False, indent=2)

print('Vendedores:', len(vendors))
for v in vendors:
    print(v['code'], '|', v['prov'], '|', v['name'])
