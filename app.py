# -*- coding: utf-8 -*-
"""
Created on Thu Jun 26 10:26:20 2025
"""

# Importation des bibliothèques nécessaires
import streamlit as st  # Pour créer l'interface web
import json             # Pour manipuler le format GeoJSON
import pandas as pd     # Pour créer le tableau Excel
from shapely.geometry import shape, Polygon, MultiPolygon, mapping  # Pour manipuler les géométries
from shapely.ops import transform  # Pour reprojeter les géométries
from pyproj import Transformer, CRS  # Pour gérer les systèmes de coordonnées
import fiona  # Pour lire les fichiers GeoJSON avec leurs métadonnées
import tempfile  # Pour créer des fichiers temporaires
from io import BytesIO  # Pour manipuler des fichiers en mémoire (Excel, GeoJSON)

# Configuration de la page Streamlit
st.set_page_config(page_title="GeoJSON vers Excel pour Superset", layout="centered")

# Titre et explication utilisateur
st.title("GeoJSON vers Excel pour Superset")
st.markdown("Vous pouvez déposer un fichier GeoJSON, cela permettra de :")
st.markdown("Créer un fichier .xlsx que vous pourrez télécharger, afin de l'uploader en base dans Superset. Cela signifie que le fichier GeoJSON chargé :")
st.markdown("- verra ses géométries être simplifiées (si nécessaire) pour éviter les erreurs Excel, qui dispose d'un nombre limité de caractères par case.")
st.markdown("- verra ses multipolygones être éclatés en polygones, car mal gérés par Superset (v4.1.2).")
st.markdown("- sera reprojeté automatiquement en WGS 84 (EPSG:4326), car Superset ne gère pas encore les autres projections.")

# Zone d'upload du fichier
uploaded_file = st.file_uploader("Déposez un fichier GeoJSON", type=["geojson"])

if uploaded_file is not None:
    # Lire le contenu une seule fois
    content = uploaded_file.read()

    if not content.strip():
        st.error("Le fichier est vide. Assurez-vous que ce n'est pas un téléchargement vide.")
    else:
        uploaded_file.seek(0)  # Au cas où Fiona relit depuis le début

    # Créer un fichier temporaire pour que Fiona puisse le lire
    with tempfile.NamedTemporaryFile(delete=False, suffix=".geojson") as tmp_input:
        # Lire le contenu du fichier uploadé et l’écrire dans le fichier temporaire
        tmp_input.write(content)
        tmp_input.flush()

        try:
            # Ouverture du GeoJSON avec Fiona (qui permet de lire les métadonnées comme le CRS)
            with fiona.open(tmp_input.name, 'r') as src:
                crs_dict = src.crs  # Extraction du système de coordonnées (CRS)
                features = list(src)  # Liste des entités géographiques

            # Si aucun CRS n’est défini, on suppose EPSG:4326 (WGS 84)
            if not crs_dict:
                st.warning("CRS non détecté, utilisation par défaut : EPSG:4326")
                source_crs = CRS.from_epsg(4326)
            else:
                source_crs = CRS.from_user_input(crs_dict)
                st.success(f"CRS détecté : {source_crs.to_string()}")

            # Définition du CRS cible : WGS 84 (EPSG:4326)
            target_crs = CRS.from_epsg(4326)
            transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)

            # Fonction de reprojection d'une géométrie
            def reproject(geom):
                return transform(transformer.transform, geom)

            # Compte le nombre total de points dans un polygone
            def total_coords_count(geom):
                if isinstance(geom, Polygon):
                    rings = [geom.exterior] + list(geom.interiors)
                    return sum(len(ring.coords) for ring in rings)
                return 0

            # Simplifivation adaptative d’un polygone jusqu'à atteindre un certain nombre de points
            def adaptive_polygon_simplify(geom, target_points=780, max_iterations=300):
                original = total_coords_count(geom)
                if original <= target_points:
                    return geom, 0.0, original, original

                tolerance = 1e-10  # Tolérance initiale très faible
                simplified = geom.simplify(tolerance, preserve_topology=True)
                iteration = 0

                while total_coords_count(simplified) > target_points and iteration < max_iterations:
                    error_ratio = total_coords_count(simplified) / target_points
                    tolerance *= min(error_ratio, 2)
                    simplified = geom.simplify(tolerance, preserve_topology=True)
                    iteration += 1

                simplified_n = total_coords_count(simplified)
                return simplified, tolerance, original, simplified_n

            # Préparation des listes de données pour Excel et GeoJSON
            all_records = []
            simplified_features = []

            # Parcours de chaque entité géographique du fichier
            for i, feature in enumerate(features):
                if feature is None:
                    st.warning(f"L'entité #{i} est vide (None). Elle est ignorée.")
                    continue

                # Nouvelles vérifications explicites
                if "geometry" not in feature:
                    st.error(f"L'entité #{i} ne contient pas de clé 'geometry'.")
                    continue
                if "properties" not in feature:
                    st.error(f"L'entité #{i} ne contient pas de clé 'properties'.")
                    continue
                if feature.get("geometry") is None:
                    st.warning(f"L'entité #{i} a une géométrie nulle. Elle est ignorée.")
                    continue
                if not feature.get("properties"):
                    st.warning(f"L'entité #{i} n'a pas de propriété. Elle est traitée sans attribut.")

                try:
                    # Récupération sécurisée des propriétés
                    raw_props = feature.get("properties") or {}
                    props = dict(raw_props)

                    # Conversion de la géométrie au format shapely + reprojection
                    geom = shape(feature["geometry"])
                    geom = reproject(geom)

                    # Éclatement des MultiPolygon en plusieurs Polygons
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
                            f"{orig_pts}→{simp_pts} points (tolérance={tol:.0e})"
                            if tol > 0 else "Aucune simplification"
                        )

                        all_records.append(record)

                except Exception as sub_e:
                    st.error(f"Erreur avec l'entité #{i} : {sub_e}")

            # Création d’un fichier Excel à partir des enregistrements
            df = pd.DataFrame(all_records)
            excel_buffer = BytesIO()
            df.to_excel(excel_buffer, index=False, engine='openpyxl')
            excel_buffer.seek(0)

            # Création d’un GeoJSON simplifié
            final_geojson = {
                "type": "FeatureCollection",
                "features": simplified_features
            }
            geojson_str = json.dumps(final_geojson, ensure_ascii=False, indent=2)
            geojson_bytes = geojson_str.encode("utf-8")

            # Affichage des boutons de téléchargement
            st.success("Conversion réussie. Fichiers prêts à être téléchargés :")
            st.download_button("Télécharger Excel (.xlsx)", data=excel_buffer, file_name="superset_ready.xlsx")
            st.download_button("Télécharger GeoJSON simplifié", data=geojson_bytes, file_name="simplified.geojson")

        except Exception as e:
            # En cas d'erreur quelconque, afficher le message d'erreur
            st.error(f"Erreur lors du traitement : {e}")
