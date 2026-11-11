### Product Features

**High:**
* Process eBird data (use API)
* Process garmin data

**Medium:**
* Build waypoint summaries from all data embeddings
* Simple "near here" map click search to take advantage of location indexes
* Play around with agentic flow for chat bot

### Product Improvements/Fixes

**Medium:**
* Improve copy on waypoint search (what can you search for and what do results mean, as well as "first search may take a while...")
* Waypoint search: explore hiding results with a large distance
* Explore different embeddings
* There is no public location calculated for tracks/track points
* Get iNaturalist data in DB
* Slow API calls on initial page load lead to waypoints not showing until page refresh
* Day trip start/end dates are not handled properly; also check photo assignment logic

**Low:**
* Sort inaturalist observations by rarity so the rarest in a region shows up on top
* Certain ebird hotspot entries can't be filtered by trip when using the current FE date filter (see console)

### Internal

**Medium:**
* Set up a local test DB
* Python tests for data transformation (and maybe full integration test?)
* Explore further Claude configuration

**Low:**
* Explore using gorm
* Split up (and clean up) logic in frontend file
* Explore moving frontend logic to travel repo (source as link, or copy with gh actions, or other?)

### Data

**High:**
* Fix track in Japan, Vietnam, Virachey
* Check waypoint dates: Virachey to 12/18, Banlung to 12/19, then Don Det
* Finish privacy-screening photos
* Victoria waypoints need different names for better obfuscation - can then make the second one a named location in the config file, and then change the "ghost zone overlap" warn back to an error

**Medium:**
* Scan for photo-waypoint link accuracy (checkout branches: BE waypoints-list-photo-urls and FE waypoint-two-photos)
* Make sure that waypoints with special characters in their name are handled correctly - populated with description in the DB, search working, FE popup display working

**Low:**
* A few photos don't have time_taken - missing from exif? Leads to no waypoint assignment


### Infrastructure

**Medium:**
* Check cold start response times; might want something to ping my health endpoint regularly (14 minutes?) to keep render from going to sleep; maybe just during the day
* I think the bot control I was hoping for doesn't apply to my API, since the CNAME isn't forwarded - double check that
* Expose embedding_service to public internet so I can bypass Workers AI when my computer is on?
* Auto-deploy seems broken on render (see excluded/included paths)

**Low:**
* Neon won't automatically update my schema or data
* CI (github actions with service container?)