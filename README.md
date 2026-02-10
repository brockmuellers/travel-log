# travel-log

Visualization and insights from my sabbatical travels.

## Goals

After spending 18 months travelling as a sabbatical, I have collected an immense amount of data. I want to create an enriched map-based travel log that integrates all of my data sources. Perhaps there will be insights available from it, but if not, it will be a cool keepsake.

I'm interested in learning more about modern ML workflows (vectorization and RAG, for example), as well as handling spatial data, so I'll shoehorn those subjects in.

A super stretch goal: a flythrough video that follows the main GPX track and displays the most interesting data/insights for each location.

## Data Sources

Primary data sources:
* A GPX track of the entire journey, including modes of transport, pulled from FindPenguins. This route is highly simplified: it is simply our major destinations connected by the routes we took between them. Since we spent multiple days at each destination, the timestamps precision is only accurate to the destination level - not to the minute or even to the day.
* An immense number of photos, mostly geotagged
* Many eBird checklists and iNaturalist observations, eBird lifelist data
* Garmin data, including activities (mostly hikes), step count, sleep data, and HRV data
* Patchy travel notes

Secondary sources:
* My husband's travel blog, with one post per country - might be useful to provide context for an LLM
* Google location history - this is not super accurate (there are major gaps and drift)
* Data from public sources: global eBird and iNaturalist observations, other biodiversity data sources, weather + sunrise/sunset + AQI, OpenStreetMap "Points of Interest", major events (GDELT Project?), holidays, government travel advisories, opinionated travel content from Wikivoyage, Alltrails
* Data from non-public sources (can't share it, but it would be interesting to view in a local implementation): Strava/Gaia heatmaps, Lonely Planet & Rough Guide guidebooks

## Known Unknowns & Risks

* Safely handling privacy concerns - I can handle obfuscating sensitive locations but there may be other sensitive data hiding
* Timestamp precision - some data is precisely timestamped but other data is only accurate to the day; plus sources may use different time zones, and we frequently switched time zones
* Geotagging precision - the garmin tracks, and to a lesser extent geotagged photos, are the only sources I really trust
* Data visualization - UI is not my area of expertise, and there's a lot of fuzzy data here
* I've never worked with embedded vector data and RAG, but want to give it a shot for this project. I have no idea how large an undertaking this is - I can definitely create something nominally functional, but will that really give me the fun insights I'm looking for? Maybe I'll need to go the classical data science route for that.
* Infrastructure - this is feasible to run locally, but I want to share the resulting product with others. Can I do this with free or almost-free infrastructure? A cursory glance says yes, particularly with access controls, but I need to do more research.

## Project Phases

* "Hello World" travel map
    * Simple map on my static Github Pages website, with all resources stored directly in the repo
    * Download, process, and display the easy data - FindPenguins GPX, eBird checklists
    * Obfuscate sensitive locations
    * Use a mapping library that will scale
* Local database & ETL
    * Probably postgres with postgis and pgvector - nice and flexible
    * Play around with what data to use, preliminary data models, intermediate processing steps, and of course vector embeddings
    * Play around with queries and RAG (might just use RAG for pre-computed summaries for simplicity)
* Visualization
    * Set up a super basic server for the database (go would be easy for me)
    * Lots of visual design decisions to make here! Ideally it can all be built on top of the initial map, and I'll keep v1 as simple as I reasonably can
    * Probably will need to heavily alter data models as I make UI decisions
* Public deployment
    * Decide on free/low-cost infrastructure for the database and server, keeping privacy and authorization in mind
    * Deploy and share!

The timeline for this project is immensely flexible, but I'm aiming to timebox v1: <1 week for the initial travel map, ~2 weeks for the local database and data exploration, 1-2 weeks for visualization, and <1 week for deployment. It isn't a production system so I won't have production quality, or production timelines.
