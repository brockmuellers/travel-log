### Product Features

**High:**
* Download and process eBird data

**Medium:**
* Build waypoint summaries from all data embeddings
* Simple "near here" map click search to take advantage of location indexes

### Product Improvements/Fixes

**High:**
* Partly broken inaturalist obfuscation - not essential so could either remove or fix

**Medium:**
* Improve copy on waypoint search (what can you search for and what do results mean, as well as "first search may take a while...")
* Filter waypoint search by selected "trip" tab
* Display clickable waypoints on map
* Explore different embeddings

### Internal

**Medium:**
* Split up (and clean up) logic in frontend file
* Explore moving frontend logic to travel repo (source as link, or copy with gh actions, or other?)
* Set up a local test DB
* Python tests for data transformation (and maybe full integration test?)
* Explore further Claude configuration
* Duplicated point obfuscation code in db waypoint reload scripts?

### Data

**High:**
* Fix track in Japan and Vietnam
* Fix Vietnamese waypoint name with special characters (and find any others?)
* Fill in beginning waypoints for West Coast trip (update trips.json too)
* Finish privacy-screening photos
* Smarter photo-to-waypoint linking for travel days and time changes!

**Low:**
* A few photos don't have time_taken - missing from exif?


### Infrastructure

**Medium:**
* Check cold start response times; might want something to ping my health endpoint regularly (14 minutes?) to keep render from going to sleep; maybe just during the day
* I think the bot control I was hoping for doesn't apply to my API, since the CNAME isn't forwarded - double check that
* Expose embedding_service to public internet so I can bypass huggingfaces when my computer is on?

**Low:**
* Neon won't automatically update my schema or data
* CI (github actions with service container?)