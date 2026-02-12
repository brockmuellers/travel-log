import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
import os
import sys
from pathlib import Path
import srtm
import contextlib

# Initialize elevation data (files will be cached in a local directory)
elevation_data = srtm.get_data()

NS = {'gpx': 'http://www.topografix.com/GPX/1/1'}

def connect_to_database(db_params):
    """ Connect to the PostgreSQL database server and return a connection object. """
    conn = None
    try:
        print('Connecting to the PostgreSQL database...')
        conn = psycopg2.connect(**db_params)
        print('Connection successful.')
        return conn
    except (Exception, psycopg2.DatabaseError) as error:
        print(f"Error connecting to the database: {error}")
        if conn:
            conn.close()
        return None

def get_text(elem, tag):
    item = elem.find(tag, NS)
    return item.text if item is not None else None
    
def run_findpenguins_gpx_etl(conn, file_path):
    """ Import data from FindPenguins GPX file given its path and a DB connection object """
    tree = ET.parse(file_path)
    root = tree.getroot()
    ns = {'gpx': 'http://www.topografix.com/GPX/1/1'}
    
    # 1. Parse Waypoints (already ordered by time in the input)
    waypoints = []
    for wpt in root.findall('gpx:wpt', NS):
        waypoints.append({
            'name': get_text(wpt, 'gpx:name'),
            'desc': get_text(wpt, 'gpx:desc'),
            'time': get_text(wpt, 'gpx:time'),
            'lat': float(wpt.get('lat')),
            'lon': float(wpt.get('lon'))
        })
    
    # Sort waypoints by time to ensure we can find the "previous" one easily
    # UNNECESSARY, they are already ordered
    #waypoints.sort(key=lambda x: x['time'])

    # 2. Parse Tracks (Grouped by Timestamp)
    raw_points = root.findall('.//gpx:trkpt', NS)
    grouped_tracks = defaultdict(list)
    
    for pt in raw_points:
        timestamp = get_text(pt, 'gpx:time')
        grouped_tracks[timestamp].append({
            'time': timestamp,
            'lat': float(pt.get('lat')),
            'lon': float(pt.get('lon')),
            'ele': get_text(pt, 'gpx:ele')
        })

    sorted_timestamps = sorted(grouped_tracks.keys())
    
    # --- DATABASE INSERTION ---
    cur = conn.cursor()

    # A. Insert Trip
    # TODO verify times
    trip_name = root.find('gpx:metadata', NS).find('gpx:name', NS).text
    print(f"Inserting Trip {trip_name}...")
    cur.execute("""
        INSERT INTO trips (name, start_date, end_date)
        VALUES (%s, %s, %s) RETURNING id
    """, (trip_name, waypoints[0]['time'], waypoints[-1]['time']))
    trip_id = cur.fetchone()[0]
    
    # B. Insert Waypoints & Build Lookup Map
    # Lookup map will be used to figure out which start/end waypoints correspond to a track
    print("Inserting Waypoints...")
    # Map: timestamp_string -> database_id
    time_to_wp_id = {}
    
    # Also keep a list of (timestamp, id) tuples to look up the "previous" waypoint
    wp_timeline = [] 

    # A waypoint's end time should be the start time of the next waypoint
    waypoint_end_times = [None] * len(waypoints) # last waypoint has no end time
    for i, val in enumerate(waypoints):
        if i != len(waypoints) - 1:
            waypoint_end_times[i] = waypoints[i+1]['time']
        
    for i, wp in enumerate(waypoints):
        cur.execute("""
            INSERT INTO waypoints (trip_id, name, description, start_time, end_time, location)
            VALUES (%s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            RETURNING id
        """, (trip_id, wp['name'], wp['desc'], wp['time'], waypoint_end_times[i], wp['lon'], wp['lat']))
        
        wp_id = cur.fetchone()[0]
        time_to_wp_id[wp['time']] = wp_id
        wp_timeline.append((wp['time'], wp_id))
        
    # C. Insert Tracks linked to Waypoints, as well as Points
    print("Inserting Tracks...")
    for i, ts in enumerate(sorted_timestamps):
        points = grouped_tracks[ts]
        
        # Determine Waypoint Links
        # The track ends at the waypoint with the matching timestamp
        end_wp_id = time_to_wp_id.get(ts) 
        
        # The track starts at the previous waypoint in the timeline
        # If this is the first track segment, start_wp might be None or the first waypoint itself
        start_wp_id = None
        
        # Find the index of the current timestamp in our waypoint timeline
        # We iterate to find where 'ts' fits. 
        # (In your file, track_time usually equals waypoint_time, so we look for exact match)
        current_wp_index = next((idx for idx, val in enumerate(wp_timeline) if val[0] == ts), None)
        
        if current_wp_index is not None and current_wp_index > 0:
            start_wp_id = wp_timeline[current_wp_index - 1][1]
        
        # Construct Geometry
        if len(points) > 1:
            coords = ", ".join([f"{p['lon']} {p['lat']}" for p in points])
            wkt = f"LINESTRING({coords})"
        else:
            p = points[0]
            wkt = f"LINESTRING({p['lon']} {p['lat']}, {p['lon']} {p['lat']})"

        # Insert Track
        cur.execute("""
            INSERT INTO tracks 
            (trip_id, start_waypoint_id, end_waypoint_id, source, start_time, end_time_incl, route)
            VALUES (%s, %s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326))
            RETURNING id
        """, (
            trip_id, 
            start_wp_id, 
            end_wp_id, 
            'FindPenguins', 
            points[0]['time'], 
            points[-1]['time'], 
            wkt
        ))
        track_id = cur.fetchone()[0]
        
        # Insert Points
        # Hydrate elevation data
        for p in points:
            try:
                # Redirect stdout to devnull to silence the library's print statements
                with contextlib.redirect_stdout(open(os.devnull, 'w')):
                    p['ele'] = elevation_data.get_elevation(p['lat'], p['lon'])
            except Exception as e:
                print(f"failed to load elevation: {e}")
                continue
                
        db_points = [(track_id, p['time'], p['lon'], p['lat'], p['ele']) for p in points]
        execute_values(cur, """
            INSERT INTO track_points (track_id, recorded_at, location, elevation_meters)
            VALUES %s
        """, db_points, template="(%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s)")
        
    conn.commit()

if __name__ == "__main__":
    load_dotenv()
    # NOTE: USING RAW UN-OBFUSCATED GPX FILES FOR NOW
    DEFAULT_GPX_DIR = os.path.join(os.getenv("PRIVATE_DATA_DIR"),"findpenguins")
    
    files_list = list(Path(DEFAULT_GPX_DIR).glob("*.gpx"))
    
    print(f"Importing {len(files_list)} files")
    
    # TODO remove this debug line
    #files_list = files_list[0:1]
    
    db_params = {
        "host": os.getenv("DATABASE_HOST"),   
        "database": os.getenv("DATABASE_NAME"),
        "user": os.getenv("DATABASE_USER"),  
        "password": os.getenv("DATABASE_PASSWORD"),
        "port": os.getenv("DATABASE_PORT")
    }
    connection = connect_to_database(db_params)
    if connection is None:
        sys.exit(1)
    
    for f in files_list:
        print(f"Processing {f}...")
        try:
            run_findpenguins_gpx_etl(connection, f)
            print("Success!")
        except Exception:
            connection.close()
            print("Failed to process")
            raise
    
    connection.close()
