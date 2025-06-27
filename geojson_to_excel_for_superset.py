# Script pour transformer un GeoJSON en fichier Excel et GeoJSON simplifié.
# Objectif : préparer les données pour Superset (deck.gl), en reprojetant,
# simplifiant et éclatant les multipolygones.

import json
import pandas as pd
from shapely.geometry import shape, Polygon, MultiPolygon, mapping
from shapely.ops import transform
from pyproj import Transformer, CRS
import fiona  # pour lire les fichiers GeoJSON avec détection automatique du CRS

# Fonction qui lit un fichier GeoJSON et récupère :
# -la liste des entités géographiques (features)
# -le système de projection (CRS) détecté
def read_features_and_crs(input_geojson_path):
    with fiona.open(input_geojson_path, 'r') as src:
        crs_dict = src.crs  # extraction du CRS (système de coordonnées)
        if not crs_dict:
            # Si le CRS est absent, on suppose qu'il est déjà en WGS 84
            print("CRS inconnu. Supposition : EPSG:4326")
            source_crs = CRS.from_epsg(4326)
        else:
            # Conversion du CRS au format pyproj
            source_crs = CRS.from_user_input(crs_dict)
            print(f"CRS détecté : {source_crs.to_string()}")

        # Chargement de toutes les entités du fichier
        features = list(src)
    return features, source_crs

# Fonction utilitaire qui compte le nombre total de points d’un polygone
# (ligne extérieure + éventuels trous à l'intérieur du polygone)
def total_coords_count(geom):
    if isinstance(geom, Polygon):
        rings = [geom.exterior] + list(geom.interiors)
        return sum(len(ring.coords) for ring in rings)
    return 0

# Fonction de simplification adaptative d’un polygone
# pour réduire le nombre de points sous une limite cible (par ex. 780)
# utile pour rester sous la limite de caractères d’Excel
def adaptive_polygon_simplify(geom, target_points=780, max_iterations=300):
    original = total_coords_count(geom)

    # Si déjà assez simple, on garde la géométrie telle quelle
    if original <= target_points:
        return geom, 0.0, original, original

    tolerance = 1e-10  # Tolerance initiale très faible
    simplified = geom.simplify(tolerance, preserve_topology=True)
    iteration = 0

    # Boucle : si geometrie complexe, on augmente la tolérance jusqu’à obtenir une géométrie assez simple
    while total_coords_count(simplified) > target_points and iteration < max_iterations:
        error_ratio = total_coords_count(simplified) / target_points
        tolerance *= min(error_ratio, 2)  # on augmente progressivement
        simplified = geom.simplify(tolerance, preserve_topology=True)
        iteration += 1

    simplified_n = total_coords_count(simplified)
    return simplified, tolerance, original, simplified_n

# Fonction principale qui convertit un GeoJSON en fichier Excel et GeoJSON simplifié
def geojson_to_excel_with_exploded_multipolygons(input_geojson_path, output_excel_path, output_geojson_path):
    # Lecture du fichier et détection du CRS
    features, source_crs = read_features_and_crs(input_geojson_path)

    # Définition du système de coordonnées cible : EPSG:4326 (WGS 84)
    target_crs = CRS.from_epsg(4326)

    # Création du transformateur de coordonnées
    transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)

    # Fonction interne de reprojection shapely
    def reproject(geom):
        return transform(transformer.transform, geom)

    # Listes pour stocker les lignes Excel et les features simplifiées
    all_records = []
    simplified_features = []

    # Parcours de chaque entité géographique
    for feature in features:
        props = dict(feature["properties"])  # récupération des attributs
        geom = shape(feature["geometry"])    # conversion JSON en shapely
        geom = reproject(geom)               # reprojection vers WGS 84

        # Éclatement des multipolygones en plusieurs polygones car superset gère mal les multipolugones
        if isinstance(geom, MultiPolygon):
            polys = list(geom.geoms)
        elif isinstance(geom, Polygon):
            polys = [geom]
        else:
            # Si ce n’est ni un polygone ni un multipolygone, on ignore
            continue

        # Pour chaque polygone (issu d’un éventuel éclatement)
        for poly in polys:
            # Simplification adaptative de la géométrie
            simplified_geom, tol, orig_pts, simp_pts = adaptive_polygon_simplify(poly)

            # Conversion de la géométrie en format GeoJSON
            geom_json = mapping(simplified_geom)

            # Création d’une nouvelle entité (feature) simplifiée
            feature_geojson = {
                "type": "Feature",
                "geometry": geom_json,
                "properties": props
            }
            simplified_features.append(feature_geojson)

            # Préparation d’un enregistrement (ligne) pour le tableau Excel
            record = props.copy()
            record_geojson = {
                "type": "Feature",
                "geometry": geom_json
            }

            # On stocke la géométrie en texte (format JSON compact) dans une cellule
            record["geometry"] = json.dumps(record_geojson, ensure_ascii=False, separators=(',', ':'))

            # Ajout d’une colonne pour suivre le niveau de simplification
            record["simplification_info"] = (
                f"{orig_pts}→{simp_pts} points (tolérance={tol:.0e})"
                if tol > 0 else "Aucune simplification"
            )

            # Ajout de la ligne au tableau final
            all_records.append(record)

    # Export final du tableau vers un fichier Excel
    df = pd.DataFrame(all_records)
    df.to_excel(output_excel_path, index=False)
    print(f"Excel exporté : {output_excel_path}")

    # Export du GeoJSON simplifié vers un fichier
    final_geojson = {
        "type": "FeatureCollection",
        "features": simplified_features
    }
    with open(output_geojson_path, "w", encoding="utf-8") as f:
        json.dump(final_geojson, f, ensure_ascii=False, indent=2)
    print(f"GeoJSON simplifié exporté : {output_geojson_path}")

# chemins des fichiers
if __name__ == "__main__":
    geojson_to_excel_with_exploded_multipolygons(
        input_geojson_path=r"C:/Users/lgrillon/Downloads/znieff-type1.geojson", #chemin du geojson chargé
        output_excel_path=r"C:/Users/lgrillon/Downloads/zniefff.xlsx", #chemin du fichier excel en sortie
        output_geojson_path=r"C:/Users/lgrillon/Downloads/zniefff.geojson" #chemin du fichier geojson issu de la simplification en sortie
    )

