import pandas as pd
import csv
import sys

# Filter the GBIF report (which is a 100+ mb file) to only include ones relevant to my personal observations.
# This is 80% Gemini Pro.
# Ultimately it may have been simpler to use the inaturalist API but here we are.
# Update: I'm changing tacks to use the inaturalist API to get observation counts, but will
# leave this here in case I want other info from GBIF reports.

# --- CONFIGURATION ---
# My iNaturalist observations file
inat_file = 'personal_data/inaturalist/observations-679011.csv'

# The large GBIF report file
gbif_file = 'public_data/datasets/gbif_inaturalist_research_grade_observations_species_list_2026-02-10.csv'

# The output file name
output_file = 'personal_data/inaturalist/filtered_gbif_inaturalist_species_list.csv'

# GBIF exports are often Tab-Separated Values (TSV) even if named .csv
delimiter = '\t'

# Strict species matching or a more fuzzy version?
# Actually I need to think more about this - a lot of my observations are not species / subspecies level
strict_mode = True

# Manually mapping a few GBIF species names to inaturalist names
special_matches = {
    "Castilleja rhexifolia": "Castilleja rhexiifolia",
    "Erethizon dorsatus": "Erethizon dorsatum",
    "Glyphodes onychinalis": "Chabulina onychinalis",
    "Lycalopex gymnocercus": "Lycalopex grisea",
    "Lymantria ganara": "Lymantria ganara xiaolingensis",
}
# ---------------------

def filter_gbif_robust():
    print(f"Reading targets from {inat_file}...")
    
    try:
        df = pd.read_csv(inat_file)
        
        # 1. Strict Filter: Only look for Species or Subspecies
        # We drop any row that doesn't have a species name (e.g. Family/Genus level IDs)
        # This prevents the "8000 rows" issue where "Falco" matches all falcons.
        df_species = df.dropna(subset=['taxon_species_name'])
        
        # 2. Build Target List
        # We collect both the scientific name and the species name to catch everything.
        # EDIT: skipping species name for now, scientific name seems good enough
        target_names = set()
        if 'scientific_name' in df_species.columns:
            target_names.update(df_species['scientific_name'].astype(str).str.strip())
        #if 'taxon_species_name' in df_species.columns:
        #    target_names.update(df_species['taxon_species_name'].astype(str).str.strip())
            
        print(f"Found {len(target_names)} unique species/subspecies targets.")
        print(f"Skipping {len(df) - len(df_species)} non-species entries.")
        
    except FileNotFoundError:
        print(f"Error: Could not find {inat_file}.")
        return

    print(f"Processing {gbif_file}...")
    
    found_names = set()
    match_count = 0
    
    try:
        with open(gbif_file, 'r', encoding='utf-8', errors='replace') as fin, \
             open(output_file, 'w', encoding='utf-8', newline='') as fout:
            
            reader = csv.DictReader(fin, delimiter=delimiter)
            
            if not reader.fieldnames:
                print("Error: Could not read headers.")
                return
            
            writer = csv.DictWriter(fout, fieldnames=reader.fieldnames, delimiter=delimiter)
            writer.writeheader()
            
            for row in reader:
                # We need to construct "candidates" from the GBIF row to check against our targets.
                candidates = set()
                
                # A. Get raw values from GBIF
                species_col = row.get('species', '').strip()
                sci_name = row.get('scientificName', '').strip()
                
                # Decide if this is a subspecies, so we know to match on trinomial
                tax_rank = row.get('taxonRank', '').strip()
                is_subspecies = tax_rank == "SUBSPECIES"
                              
                
                # B. Add exact values to candidates set (e.g. "Mahonia nervosa")
                # The species col is the most frequent exact match so just using that
                if species_col: 
                    candidates.add(species_col)
                
                    # B.1. If this is one of the "special matches", add the hardcoded
                    # inaturalist value to our candidates list
                    if species_col in special_matches:
                        candidates.add(special_matches[species_col])
                
                # C. Generate "Clean" scientific names and add to candidates set
                # Scientific name may differ from the GBIF species name but often includes
                # authorship (e.g. "Saguinus weddelli melanoleucus (Miranda Ribeiro, 1912)")
                if sci_name:
                    parts = sci_name.split()
                    # Add binomial and trinomial candidates
                    # "Berberis nervosa (Pursh)" -> 
                    #    ("Berberis nervosa", "Berberis nervosa (Pursh)")
                    # "Saguinus weddelli melanoleucus (Miranda Ribeiro, 1912)" ->
                    #    ("Saguinus weddelli", "Saguinus weddelli melanoleucus")
                    # CAUTION: including "Saguinus weddelli" here might mean that this trinomial
                    # species row inappropriately matches a "Saguinus weddelli" entry
                    if is_subspecies:
                        if len(parts) >= 3:
                            candidates.add(f"{parts[0]} {parts[1]} {parts[2]}") # "Falco columbarius suckleyi"
                    else:
                        # Skip single-word genera
                        if len(parts) >= 2:
                            candidates.add(f"{parts[0]} {parts[1]}") # "Berberis nervosa"
                    """
                    if len(parts) >= 2:
                        candidates.add(f"{parts[0]} {parts[1]}") # "Berberis nervosa"
                    if len(parts) >= 3:
                        candidates.add(f"{parts[0]} {parts[1]} {parts[2]}") # "Falco columbarius suckleyi"
                    """
                    


                # D. Check for Match
                # If ANY candidate is in our target list, keep the row.
                match = candidates.intersection(target_names)
                
                if len(match) > 1:
                    print(f"found multiple matches: {match}")
                
                if match:
                    writer.writerow(row)
                    match_count += 1
                    found_names.update(match)
                    

    except Exception as e:
        print(f"An error occurred: {e}")
        return

    print(f"Done! Wrote {match_count} matching rows to {output_file}.")
    
    missing = target_names - found_names
    if len(missing) > 0:
        print(f"\nStats: Found matches for {len(found_names)} names. Missed {len(missing)} names.")
        print("First missing names (check these manually):")
        for name in list(missing)[:20]:
            #print(f" - {name}")
            print(f"{name},")

if __name__ == "__main__":
    filter_gbif_robust()

