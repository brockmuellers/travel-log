2026/02/10

I spent so much time trying to match species in the GBIF dataset to my inaturalist observations. The dataset was ostensibly of inaturalist observations, so you'd think they would match up neatly. In the end I had to go back to my initial plan of using the inaturalist API to get species observation counts. Maybe the GBIF dataset processing script will be useful for something else, but I did learn a bit about taxonomy and working with messy data sources. (If only I were a data scientist - this would be much easier!)

My data directory is getting quite messy - lots of scripts, datasets, data downloads, and intermediate products. It's unclear what depends on what. This will cease to be feasible soon. I'm also repeating "deploy" logic in different scripts.

2026/02/11

It wasn't too hard to reorg the data directory. Plus, hooray for .env files! Amazing how a little bit of organization can enable scaling up.

Getting a local instance of postgres running. (Why postgres? I'm not quite sure what I want to do with my data yet - access patterns, types of data stored, etc - so I'm choosing a flexible and tried-and-true solution.) Just using a raw sql schema for now while I play around with data. Note: to wipe and rebuild the DB after SQL changes, `docker compose down -v  && docker compose up -d --build`.

Doing my best not to over-engineer the schema/ETL from the beginning. Some sticky points:
- What's the best way to store the tracks? Track points vs inter-waypoint segments vs whole-trip tracks? Going with all of the above for now.
- How will RAGs come in? Just adding embedding vectors and metadata everywhere.
- All of the AI-suggested indices may be overkill, but I'll evaluate them later.
- There's timestamp "clumping" in the FindPenguins tracks - all points between two adjacent waypoints have the same timestamp. I wonder if I should add millisecond differences in the file?
- Privacy - right now I have a script that alters the GPX and sensitive points stored in a file, but my DB could have "private zones" stored and do screening on the fly. Less robust perhaps?
