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

2026-02-12

Time to pull in LLMs! I'm starting with waypoint descriptions, generated from my husband's travel blog. I've had much better luck with manually using Gemini Pro chat for this sort of thing, instead of anything free, but this will only be scalable if I ultimately choose something with an API. Just doing proof of concept for now, so manual is fine.

Making the semi-arbitrary choice to use the local `BAAI/bge-small-en-v1.5` for my waypoint description embeddings. I'm just trying to get something working. Choosing a local model because a) it's free, and b) privacy. (Not that the privacy makes much of a different here, since I'm using Gemini for coding help and pasting personal travel info in there all the time. But more private > less private.) We'll see if it's adequate.

2026-02-13

Playing around with semantic search of the waypoint embeddings - it works! I've done 0 customization of whatever script gemini spat out, so no doubt there are improvements. I can also find the closest match to a waypoint via the embeddings, just using SQL! So if I input a jungle waypoint, I get other jungle-based waypoints. Neat.

```.sql
SELECT 
    w2.name, 
    LEFT(w2.description, 50) as summary_snippet,
    -- (<=>) is Cosine Distance. Lower is better. 0 = Identical.
    (w1.embedding <=> w2.embedding) as distance
FROM waypoints w1, waypoints w2
WHERE w1.name = 'Virachey National Park' 
and w2.name != w1.name
ORDER BY distance ASC
limit 10;
```

2026-02-17

It isn't totally realistic to make all of the waypoint descriptions manually (though Gemini 3 Pro is worlds better than the options available in the free tier API). After a minor amount of research, I decided that Gemini has the best free tier options anyway. I spent an embarrassing amount of time tweaking my prompt to guarantee accurate output, only to discover that my string interpolation was broken and not all of my input was included. No wonder the output was strange.

Making a diagram of my data processing workflow with excalidraw (I want to see if there's a better tool out there but it works fine for now). It's reminding me that drawing a system painfully highlights unnecessary complexity.

I have just enough working that I want to get a proof of concept web app running. I have a map already on my static github pages site, but it's just using manually copied gpx/geojson files. I want to use go for the server, just to brush up on those skills. I want this to be free. Solely from chatting with gemini, I have decided that adequate options are:
* For database hosting:
	* Supabase: high free tier limits and straightforward architecture and setup; good dashboard which is probably overkill for me; annoying project "pause" after 7 days which I could get around with a github action
	* **Neon**: scales down more frequently, scales up quickly and automatically; also high free tier limits
* For backend hosting:
	* Supabase: nice if I'm using supabase for the DB but I think it doesn't support go - this [go libary](https://github.com/supabase-community/supabase-go) exists but I think that's only for DB interaction
	* Google Cloud Run: just need a docker container to upload, easy; but there's a risk of getting billed (budget alerts exist but that's probably not adequate)
	* **Render**: no dockerization required, but it sleeps after 15 min inactivity and takes a bit of time to wake up (30 seconds?); check how the free tier works - accidental charges possible?
	* Koyeb: seems very new, no credit card required; make sure that the limits are adequate
* For authentication:
	* Just a simple API key-based middleware in the go server (so bots don't blaze through my limits)
* For frontend:
	* Github pages is working just fine so I'm going to stick with it.

TODO: look into how to manage wake-up times. Cloudflare layer to block bots? Cache the super basic requests? In particular, the database usage limits might be low enough that I don't want to be waking it up all the time just because someone loads my page. Or should I enter a password somewhere on the frontend?

2026-02-19

I've been convinced to move my DNS hosting, for my entire domain, to Cloudflare. As I understand it, they will give me the aforementioned bot blocking, maybe some bonus analytics (my google analytics is broken and I don't feel like fixing it), and I can move away from the very painfully slow namecheap redirects. The bot blocking isn't a big deal for now, given that I only have a static github pages site, but it could be a bigger deal once I have an API. (And do I need the API to be on my personal domain? Probably not, but it seems nicer from a CORS perspective. Not to mention that it'll be slightly easier to swap to a different backend hosting provider if render is no good.) I'm wary of the extra layer of complexity, but Cloudflare is as trustworthy as it gets. Let's give it a shot.

Going back to my web app stack...I somehow missed the existence of Oracle Cloud's Always Free tier. I have an inkling that the setup will be more complex (less handholding) but I'm literally a pro so that's fine. I get a (relatively) big machine and plenty of flexibility. Plus, there will be none of those slow cold starts. Major, major downside - it'll reclaim your resources if they don't maintain a fairly high weekly average usage. So I'm back to Neon/Render.

I'll list a few improvements that I'll need to make later:
* I think I'll want something to ping my health endpoint regularly (14 minutes?) to keep render from going to sleep; maybe just during the day
* Neon won't automatically update my schema or data
* Have a cloudflare worker to pause traffic - got this started in the UI but I'd like it to be version controlled; probably want to use github actions

It's been a day of configuration - DNS, loading environment variables, linking services, etc - but there's an API and a database! For example: https://api.travel-log.brockmuellers.com/waypoints/count

Next steps: getting the frontend to hit this, just as proof of concept. It would be fun to expose something like the vector search script. Then I'm back to feature planning and nitty gritty data exploration.
