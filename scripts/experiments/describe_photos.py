import re
import time

import ollama

image_paths = [
    # '/home/sara/Dropbox/Pictures/phone/2025/08/2025-08-31 08.42.04.jpg',
    # '/home/sara/Dropbox/Pictures/phone/2025/08/2025-08-30 14.22.18.jpg',
    # '/home/sara/Dropbox/Pictures/phone/2025/08/2025-08-29 19.37.21.jpg',
    # '/home/sara/Dropbox/Pictures/phone/2025/08/2025-08-26 13.56.21.jpg',
    # '/home/sara/repos/travel-log/data/private/photos/2024/11/2024-11-01 13.24.11.jpg',
    # '/home/sara/repos/travel-log/data/private/photos/2024/11/2024-11-02 14.35.22.jpg',
    # '/home/sara/repos/travel-log/data/private/photos/2024/11/2024-11-05 12.44.56.jpg',
    # '/home/sara/repos/travel-log/data/private/photos/2024/11/2024-11-13 14.41.42.jpg'
    '/home/sara/repos/travel-log/data/private/photos/2024/12/2024-12-03 08.18.22.jpg',
    '/home/sara/repos/travel-log/data/private/photos/2024/12/2024-12-04 14.11.21.jpg',
    '/home/sara/repos/travel-log/data/private/photos/2024/12/2024-12-06 12.46.07.jpg',
    '/home/sara/repos/travel-log/data/private/photos/2024/12/2024-12-08 09.42.05.jpg',

]

# take into account location too
# face recognition for robin? might need diff profiles for sunglasses vs not
# Seems to cache the image processing (~15 seconds), then the description is ~5 more.
# The word "describe" in the prompt really brings on the flowery language. "Write" is better.

#model = 'moondream:v2'
#model = 'qwen2.5vl:3b'
#model = 'jyan1/paligemma-mix-224:latest' - can't run
model = 'ahmadwaqar/smolvlm2-2.2b-instruct:latest'

# ---TUNING PROMPTS---
# The tricky bit is keeping the model from getting too flowery and speculating on the image.

# The "short" caption is much longer than the one-sentence caption
#prompt = 'Write a short caption for this photo.'

# This is excellent, though quite brief, used it for a while
prompt = "Describe this image in a one-sentence caption."

# Pretty good, 15-25 seconds, can get flowery
#prompt='Describe the main subject of this image, and then describe its surroundings using the word "while" or "and". Keep the description to a single, highly detailed sentence.'

# This was a real failure - it got very flowery
#prompt = """
# Describe this image in two short sentences.
# Example 1: A golden retriever sits in a grassy field. The sun is setting behind the trees in the distance.
# Example 2: A blue ceramic mug sits on a wooden desk next to a laptop. A small steam cloud rises from the coffee.
# Now, describe this image in exactly two sentences."
# """

# Best!!! But requires the "clean_llm_caption"
prompt = """Describe this image using exactly two lines:
1. A description of the main subject.
2. A description of the background details and setting."""

# This is pretty good too
# prompt = """Describe this image using exactly two lines:

# 1. A one-sentence caption.
# 2. Notable background and foreground details."""

# Some other failures:
#prompt = "Write a highly detailed one-sentence caption for this photo, which describes both its main subject and surroundings."
#prompt = "Write a one-sentence caption for this photo. The caption should contain all details about the main subject, notable background details, and setting."

def clean_llm_caption(raw_text):
    # 1. Split the text into individual lines
    lines = raw_text.strip().split('\n')

    cleaned_sentences = []
    for line in lines:
        # 2. Use regex to remove leading numbers, dots, dashes, or spaces
        # Pattern: ^[\s\d\.\)\-]+ matches start of line, digits, dots, parens, dashes
        clean_line = re.sub(r'^\s*[\d\.\)\-]+\s*', '', line)

        if clean_line:  # Avoid adding empty lines
            cleaned_sentences.append(clean_line.strip())

    # 3. Join them back together with a single space
    return " ".join(cleaned_sentences)

print(f"Starting {model} vision test...")

for image_path in image_paths:
    start_time = time.time()

    try:
        response = ollama.chat(
            model=model,
            messages=[{
                'role': 'user',
                'content': prompt,
                'images': [image_path]
            }],
            options={
                'num_thread': 4, # Restrict to 4 physical cores, seems necessary
                'num_ctx': 1024, # Shrink the memory context window
                'temperature': 0.1, # Stop it from "thinking" too creatively to save time
                'num_predict': 120 # Forcefully stop writing after 120 tokens (~30 seconds)
            }
        )

        end_time = time.time()
        elapsed_time = end_time - start_time

        #print(f"{response['message']['content']}")
        caption = clean_llm_caption(response['message']['content'])

        print("\n--- Result ---")
        print(caption)
        print(f"Time taken: {elapsed_time:.2f} seconds")

    except Exception as e:
        print(f"An error occurred: {e}")
