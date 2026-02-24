### Product Features

**High:**
* Download and process eBird data
* Add travel mode to GPX
	* Scrape data that includes travel mode
	* Get travel mode into GPX
	* Color map lines

**Medium:**
* Explore different embeddings
* Build waypoint summaries from all data embeddings

### Product Improvements

**Medium:**
* Improve copy on waypoint search (what can you search for and what do results mean)
* Map legend
* Filter waypoint search by selected "trip" tab
* Link waypoint search results to points on map

### Internal

**Medium:**
* Refactor go server
* Split up (and clean up) logic in frontend file
* Explore moving frontend logic to travel repo (source as link)
* Set up a local test DB
* Python tests for data transformation (and maybe full integration test?)

**Low:**
* Docker issue - `docker compose up -d`; see devlog

### Data

**High:**
* Fix track in Japan and Vietnam
* Fix Vietnamese waypoint name with special characters (and find any others?)

### Documentation

**Medium:**
* Improve excalidraw (maybe interim files in same column as source files) and export to image

### Infrastructure

**Medium:**
* Check cold start response times; might want something to ping my health endpoint regularly (14 minutes?) to keep render from going to sleep; maybe just during the day
* Have a cloudflare worker to pause traffic - got this started in the UI but I'd like it to be version controlled; probably want to use github actions
* I think the bot control I was hoping for doesn't apply to my API, since the CNAME isn't forwarded - double check that

**Low:**
* Neon won't automatically update my schema or data
* CI (github actions with service container?)