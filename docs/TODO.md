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
* Simple "near here" map click search to take advantage of location indexes

### Product Improvements

**Medium:**
* Improve copy on waypoint search (what can you search for and what do results mean)
* Map legend
* Filter waypoint search by selected "trip" tab
* Display clickable waypoints on map

### Internal

**Medium:**
* Split up (and clean up) logic in frontend file
* Explore moving frontend logic to travel repo (source as link, or copy with gh actions, or other?)
* Set up a local test DB
* Python tests for data transformation (and maybe full integration test?)

### Data

**High:**
* Fix track in Japan and Vietnam
* Fix Vietnamese waypoint name with special characters (and find any others?)
* Finish privacy-screening photos

**Medium:**
* Create "waypoints" for the places in between trips, so photos are linked correctly
* Smarter photo-to-waypoint linking for travel days
* Explore better data flows, especially regarding location privacy

### Documentation


### Infrastructure

**Medium:**
* Check cold start response times; might want something to ping my health endpoint regularly (14 minutes?) to keep render from going to sleep; maybe just during the day
* Have a cloudflare worker to pause traffic - got this started in the UI but I'd like it to be version controlled; probably want to use github actions
* I think the bot control I was hoping for doesn't apply to my API, since the CNAME isn't forwarded - double check that
* Expose embedding_service to public internet so I can bypass huggingfaces when my computer is on

**Low:**
* Neon won't automatically update my schema or data
* CI (github actions with service container?)