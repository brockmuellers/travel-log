# travel-log

Visualization and insights from my sabbatical travels.

## Goals

After spending 18 months travelling as a sabbatical, I have collected an immense amount of data. I want to create an enriched map-based travel log that integrates all of my data sources. Perhaps there will be insights available from it, but if not, it will be a cool keepsake.

Primary data sources:
* A GPX track of the entire journey, including modes of transport, pulled from FindPenguins. This route is highly simplified: it is simply our major destinations connected by the routes we took between them. Since we spent multiple days at each destination, the timestamps precision is only accurate to the destination level - not to the minute or even to the day.
* An immense number of photos, mostly geotagged
* Many eBird checklists and iNaturalist observations, eBird lifelist data
* Garmin data, including activities (mostly hikes), step count, sleep data, and HRV data
* Patchy travel notes

Secondary sources:
* My husband's travel blog, with one post per country - might be useful to provide context for an LLM
* Google location history - this is not super accurate (there are major gaps and drift)
* Any data from public sources: global eBird and iNaturalist observations, weather + sunrise/sunset + AQI, OpenStreetMap "Points of Interest", major events (GDELT Project?), holidays, government travel advisories, opinionated travel context from Wikivoyage

A super stretch goal: a flythrough video that follows the main GPX track and displays the most interesting data/insights for each location.

## Known Unknowns & Anticipated Roadblocks

* Safely handling privacy concerns - I can handle obfuscating sensitive locations but there may be other sensitive data hiding
* Timestamp precision - some data is precisely timestamped but other data is only accurate to the day; plus sources may use different time zones, and we frequently switched time zones
* Geotagging precision - the garmin tracks, and to a lesser extent geotagged photos, are the only sources I really trust
* Data visualization - UI is not my area of expertise, and there's a lot of fuzzy data here
* I've never worked with embedded vector data and RAG, but want to give it a shot for this project. I have no idea how large an undertaking this is - I can definitely create something nominally functional, but will that really give me the fun insights I'm looking for? Maybe I'll need to go the data science route for that.
* Infrastructure - this is feasible to run locally, but I want to share the resulting product with others. Can I do this with free or almost-free infrastructure? A cursory glance says yes, particularly with access controls, but I need to do more research.
