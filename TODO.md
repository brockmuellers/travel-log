### Infrastructure

**Medium priority:**
* Check cold start response times; might want something to ping my health endpoint regularly (14 minutes?) to keep render from going to sleep; maybe just during the day
* Have a cloudflare worker to pause traffic - got this started in the UI but I'd like it to be version controlled; probably want to use github actions
* I think the bot control I was hoping for doesn't apply to my API, since the CNAME isn't forwarded - double check that

**Low priority:**
* Docker issue - `docker compose up -d`; see devlog
* Neon won't automatically update my schema or data