import argparse
from google import genai
from google.genai import types
import json
from dotenv import load_dotenv
import os
import time
from pydantic import BaseModel, Field


# --- Config ---
load_dotenv()

GEMINI_MODEL = 'gemini-3-flash-preview'
model_shorthand = "gemini3fp"   
# MODELS = {
#     '25fl': 'gemini-2.5-flash-lite', # cheap and adequate at OCR but bad at following instructions, 10 RPM 20 RPD
#     '25f': 'gemini-2.5-flash', # cheapish, a bit better than the lite, 5 RPM 20 RPD
#     '25p': 'gemini-2.5-pro', # pricey, got a 429 so maybe I can't use it
#     '3fp': 'gemini-3-flash-preview', # cheapish, 5 RPM 20 RPD
#     #'3pp': 'gemini-3-pro-preview', # I don't get this in the free tier
#     'manual': 'manual', # skips the API calls so I can do them manually
#     }

# thinking level is for gemini 3 models? just trying out medium for now
THINKING_CONFIG=types.ThinkingConfig(include_thoughts=True, thinking_level="medium")
# thinking budget is for gemini 2.5; -1 means dynamic thinking, 1024 would work; range maybe 0 or 512 to 24576
#THINKING_CONFIG=types.ThinkingConfig(include_thoughts=True, thinking_budget=1024)

# TODO load from file
PROMPT_TEMPLATE = """
You are an editor filling in a template that describes destinations in a trip. 
Your input:
- **Template:** [content below] a JSON list of "waypoints" (destinations) in chronological order. A placeholder "_general_" waypoint is included as well.
- **Blog Post:** [uploaded file] a story, written in chronological order, about the trip; the post includes both text and photos.

### THE WAYPOINTS TEMPLATE
{waypoints_data}

### INSTRUCTIONS
Please read the blog post and analyze the photos, then generate a first-person description for each waypoint. 
The description should be based only on the provided text and photos. Include as much information as you can.
1. **Data integrity:** CRITICAL: **DO NOT** change the 'name' or 'date' fields, or order of waypoints. Use them exactly as provided in the input, even if the blog uses a different format.
2. **Match by Date & Context:** Use the "arrival_date" in the JSON and the chronological flow of the blog to locate the correct section.
3. **Transit Info:** If there is a story about getting TO a location, include that in the destination's description.
4. **General Info:** If you find general trip reflections not tied to a specific location, put them in the "_general_" waypoint description.
5. **Citations:** Exclude citations.

### SPECIAL CASES
- **No Mention:** If a waypoint is not explicitly mentioned, attempt to identify relevant content in the blog based on chronological order and context. If there is no relevant content, set "description" to "No mention."
- **Multiple Visits:** Watch for locations visited more than once; use chronological order to map the waypoint to the correct section in the blog.

Return a **valid JSON object** that strictly matches the structure and content of the input waypoints, with only the "description" fields filled in.
"""

# root paths
waypoints_root_path = os.path.join(os.getenv("INTERIM_DATA_DIR"),"findpenguins")
pdf_root_path =  os.path.join(os.getenv("PRIVATE_DATA_DIR"),"robinblog")
output_root_path = os.path.join(os.getenv("INTERIM_DATA_DIR"),"robinblog")

# Defining json structure to prevent the LLM from modifying it:
class Waypoint(BaseModel):
    name: str
    time: str
    description: str

def validate_waypoints(input_data, output_data):
    """
    Validates that output_data matches input_data exactly, 
    except for the 'description' field which must be populated.
    
    Args:
        input_data: List[dict] or JSON string (the original source of truth)
        output_data: List[dict] or JSON string (the AI response)
        
    Returns:
        (bool, list[str]): (IsValid, List of error messages)
    """
    # 1. Normalize Strings to Objects
    # TODO shouldn't be necessary
    if isinstance(input_data, str):
        input_data = json.loads(input_data)
    if isinstance(output_data, str):
        output_data = json.loads(output_data)

    # 3. Validation Logic
    errors = []
    valid = True

    # Check Length
    if len(input_data) != len(output_data):
        valid = False
        errors.append(f"Length mismatch: Input has {len(input_data)} items, Output has {len(output_data)}.")

    # Check Content
    for i, (original, generated) in enumerate(zip(input_data, output_data)):
        for key, original_val in original.items():
            # A. Check if all original keys exist in generated output
            if key not in generated:
                errors.append(f"Item {i}: Missing key '{key}'.")
                continue

            # B. Validate Non-Description Fields (Must be Exact Match)
            if key != "description":
                if generated[key] != original_val:
                    valid = False
                    errors.append(
                        f"Item {i} mismatch on '{key}': "
                        f"Expected '{original_val}', Got '{generated[key]}'"
                    )
            
            # C. Validate Description Field (Must be Filled)
            else:
                if not generated[key] or len(generated[key].strip()) == 0:
                    errors.append(f"Item {i}: 'description' field is empty.")

    return valid, errors

def describe_waypoints(waypoints_file, pdf_file, output_file, verbose):

    # # 1. Setup API
    client = genai.Client()

    # 2. Upload PDF to Gemini (The API handles the file storage temporarily)
    # TODO: no real need to upload and delete every time; could just check if it exists
    print(f"Uploading {pdf_file}...")
    sample_file = client.files.upload(file=pdf_file)
    while sample_file.state.name == "PROCESSING":
        print("Processing file...")
        time.sleep(2)
        sample_file = client.files.get(name=sample_file.name)
    if sample_file.state.name == "FAILED":  
        raise ValueError("File upload failed.")

    # 3. Load your Waypoints JSON
    print(f"Loading {waypoints_file}...")
    with open(waypoints_file, "r") as f:
        waypoints_data = json.load(f)

    # 5. Call the Model

    print("Generating descriptions...")

    # TODO handle errors in the response
    prompt = PROMPT_TEMPLATE.format(waypoints_data=json.dumps(waypoints_data))
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[prompt, sample_file],
        config=types.GenerateContentConfig(
            # Forcing valid json output
            response_mime_type='application/json',
            response_schema=list[Waypoint],
            thinking_config=THINKING_CONFIG
        )
    )

    # 6. Cleanup (Optional but recommended)
    # Delete the file from the cloud to save storage limits
    client.files.delete(name=sample_file.name)


    """
    TODO: do I need to remove these; I did from the gemini 3 pro output
        (if so, remove the "do not include citations instruction from the prompt because it's ignoring it")
        - exact match:  [cite_start]
        - regex:        \s*\[cite:\s*([^\]]+)\]
    """

    # turn response into a list of dicts
    output = [item.model_dump() for item in response.parsed]

    # 6. Output Results as json text
    if verbose:
        print(json.dumps(output, indent=4))

    # print thinking, just for debugging
    if verbose:
        for part in response.candidates[0].content.parts:
            if not part.text:
                continue
            if part.thought:
                print("Thought summary:")
                print(part.text)
                print()

    # print validation results
    is_valid, errors = validate_waypoints(waypoints_data, output)
    if is_valid:
        print("Output valid!")
    else:
        print(f"OUTPUT INVALID!!! ERRORS: {errors}")
        print(f"\nDID NOT SAVE OUTPUT TO OUTPUT FILE")
        return

    # RATE LIMIT AVOIDANCE
    # time.sleep(COOLDOWN)

    # Write to file
    print(f"Writing {pdf_file}...")
    with open(output_file, 'w') as f:
        json.dump(output, f, indent=4)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fill in LLM-generated descriptions of waypoints based on a blog post PDF.")
    parser.add_argument("trip_name", help="Trip name, e.g. 'southeast-asia'")
    parser.add_argument("segment_names", nargs='+', help="Trip segment name, used to determine file names. E.g. '08-indonesia'. Accepts one or more names, separated by spaces.")
    parser.add_argument('-v', '--verbose', action='store_true', help="Verbose mode")
    args = parser.parse_args()

    print(f"Processing segments: {args.segment_names}")
    for segment_name in args.segment_names: 
        waypoints_filename = f"{args.trip_name}_waypoints_{segment_name}.json"
        pdf_filename = f"{args.trip_name}_{segment_name}.pdf"
        output_filename = f"{args.trip_name}_{model_shorthand}_{segment_name}.json"

        waypoints_file = os.path.join(waypoints_root_path, waypoints_filename)
        pdf_file = os.path.join(pdf_root_path, pdf_filename)
        output_file = os.path.join(output_root_path, output_filename)

        describe_waypoints(waypoints_file, pdf_file, output_file, args.verbose)