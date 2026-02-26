import time

import ollama

image_paths = [
    # '/home/sara/Dropbox/Pictures/phone/2025/08/2025-08-31 08.42.04.jpg',
    # '/home/sara/Dropbox/Pictures/phone/2025/08/2025-08-30 14.22.18.jpg',
    # '/home/sara/Dropbox/Pictures/phone/2025/08/2025-08-29 19.37.21.jpg',
    # '/home/sara/Dropbox/Pictures/phone/2025/08/2025-08-26 13.56.21.jpg',
    '/home/sara/repos/travel-log/data/private/photos/2024/11/2024-11-01 13.06.42.jpg',
    '/home/sara/repos/travel-log/data/private/photos/2024/11/2024-11-02 14.35.22.jpg',
    '/home/sara/repos/travel-log/data/private/photos/2024/11/2024-11-05 12.44.56.jpg',
    '/home/sara/repos/travel-log/data/private/photos/2024/11/2024-11-13 14.41.17.jpg'
]

# take into account location too
# face recognition for robin? might need diff profiles for sunglasses vs not
# Seems to cache the image processing (~15 seconds, then the description is ~5 more.
# The word "describe" in the prompt really brings on the flowery language. "Write" is better.

#model = 'moondream:v2'
#model = 'qwen2.5vl:3b'
#model = 'jyan1/paligemma-mix-224:latest' - can't run
model = 'ahmadwaqar/smolvlm2-2.2b-instruct:latest'

# The "short" caption is much longer than the one-sentence caption
#prompt = 'Write a short caption for this photo.'
prompt = "Describe this image in a one-sentence caption."


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

        print("\n--- Result ---")
        print(f"Caption: {response['message']['content']}")
        print(f"Time taken: {elapsed_time:.2f} seconds")

    except Exception as e:
        print(f"An error occurred: {e}")
