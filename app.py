# -*- coding: utf-8 -*-
"""
Created on Thu Jun 26 10:26:20 2025

@author: LGrillon
"""

import streamlit as st
import json
import pandas as pd
from shapely.geometry import shape, Polygon, MultiPolygon, mapping
from shapely.ops import transform
from pyproj import Transformer, CRS
import fiona
import tempfile
from io import BytesIO

st.set_page_config(page_title="GeoJSON → Superset Excel", layout="centered")

st.title("GeoJSON vers Excel pour Superset")
st.markdown("Déposez un fichier GeoJSON, cela va permettre de :")
st.markdown("1) Reprojeter automatiquement en WGS 84 (EPSG:4326)")
st.markdown("2) Simplifier les géométries pour éviter les erreurs Excel")
st.markdown("3) Éclater les MultiPolygon en Polygon")
st.markdown("4) Générer un fichier .xlsx et un GeoJSON simplifié que vous pourrez télécharger, afin de l'uploader en database sur superset.")

uploaded_file = st.file_uploader("Déposez ici un fichier GeoJSON", type=["geojson"])

if uploaded_file:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".geojson") as tmp_input:
        tmp_input.write(uploaded_file.read())
        tmp_input.flush()

        try:
            # Lecture et détection du CRS
            with fiona.open(tmp_input.name, 'r') as src:
                crs_dict = src.crs
                features = list(src)

            if not crs_dict:
                st.warning("CRS non détecté, utilisation par défaut : EPSG:4326")
                source_crs = CRS.from_epsg(4326)
            else:
                source_crs = CRS.from_user_input(crs_dict)
                st.success(f"CRS détecté : {source_crs.to_string()}")

            target_crs = CRS.from_epsg(4326)
            transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)

            def reproject(geom):
                return transform(transformer.transform, geom)

            def total_coords_count(geom):
                if isinstance(geom, Polygon):
                    rings = [geom.exterior] + list(geom.interiors)
                    return sum(len(ring.coords) for ring in rings)
                return 0

            def adaptive_polygon_simplify(geom, target_points=780, max_iterations=300):
                original = total_coords_count(geom)
                if original <= target_points:
                    return geom, 0.0, original, original

                tolerance = 1e-10
                simplified = geom.simplify(tolerance, preserve_topology=True)
                iteration = 0

                while total_coords_count(simplified) > target_points and iteration < max_iterations:
                    error_ratio = total_coords_count(simplified) / target_points
                    tolerance *= min(error_ratio, 2)
                    simplified = geom.simplify(tolerance, preserve_topology=True)
                    iteration += 1

                simplified_n = total_coords_count(simplified)
                return simplified, tolerance, original, simplified_n

            all_records = []
            simplified_features = []

            for feature in features:
                props = dict(feature["properties"])
                geom = shape(feature["geometry"])
                geom = reproject(geom)

                polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]

                for poly in polys:
                    simplified_geom, tol, orig_pts, simp_pts = adaptive_polygon_simplify(poly)
                    geom_json = mapping(simplified_geom)

                    simplified_features.append({
                        "type": "Feature",
                        "geometry": geom_json,
                        "properties": props
                    })

                    record = props.copy()
                    record_geojson = {
                        "type": "Feature",
                        "geometry": geom_json
                    }
                    record["geometry"] = json.dumps(record_geojson, ensure_ascii=False, separators=(',', ':'))
                    record["simplification_info"] = (
                        f"{orig_pts}→{simp_pts} points (tolérance={tol:.0e})" if tol > 0 else "Aucune simplification"
                    )
                    all_records.append(record)

            # Créer Excel en mémoire
            df = pd.DataFrame(all_records)
            excel_buffer = BytesIO()
            df.to_excel(excel_buffer, index=False, engine='openpyxl')
            excel_buffer.seek(0)

            # Créer GeoJSON en mémoire
            final_geojson = {
                "type": "FeatureCollection",
                "features": simplified_features
            }
            geojson_str = json.dumps(final_geojson, ensure_ascii=False, indent=2)
            geojson_bytes = geojson_str.encode("utf-8")

            st.success("Conversion réussie. Fichiers prêts à être téléchargés :")
            st.download_button("Télécharger Excel (.xlsx)", data=excel_buffer, file_name="superset_ready.xlsx")
            st.download_button("Télécharger GeoJSON simplifié", data=geojson_bytes, file_name="simplified.geojson")

        except Exception as e:
            st.error(f"Erreur lors du traitement : {e}")
