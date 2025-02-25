import os
import streamlit as st
import base64
import requests
import pandas as pd
from PIL import Image
import io
import time
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, filename="crunchbase_api.log", filemode="a",
                    format="%(asctime)s - %(levelname)s - %(message)s")

# Function to encode the image
def encode_image(image):
    if image.mode in ('RGBA', 'P'):
        image = image.convert('RGB')
    buffered = io.BytesIO()
    image.save(buffered, format="JPEG")
    return base64.b64encode(buffered.getvalue()).decode('utf-8')


# Function to get CSV from the market map using OpenAI API
def get_csv_from_image(image, api_key):
    base64_image = encode_image(image)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    payload = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Write the startups listed in the market map and categorize them into a CSV."
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    }
                ]
            }
        ],
        "max_tokens": 300
    }

    response = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
    logging.info(f"OpenAI Response: {response.json()}")
    return response.json()


# Function to get Crunchbase data for a startup with retry logic and rate limiting
def get_crunchbase_data(startup_name, crunchbase_api_key):
    search_url = f'https://api.crunchbase.com/api/v4/autocompletes?query={startup_name}'
    headers = {
        'accept': 'application/json',
        'X-cb-user-key': crunchbase_api_key
    }

    # Initial delay and retry configuration
    delay = 1  # Initial delay in seconds
    max_retries = 5  # Maximum number of retries

    for attempt in range(max_retries):
        search_response = requests.get(search_url, headers=headers)

        if search_response.status_code == 200:
            try:
                search_data = search_response.json()
                logging.info(f"Search Response: {search_data}")
                if 'entities' in search_data and search_data['entities']:
                    entity = search_data['entities'][0]
                    permalink = entity['identifier']['permalink']
                    fields = 'linkedin,website_url,short_description'
                    details_url = f'https://api.crunchbase.com/api/v4/entities/organizations/{permalink}?field_ids={fields}'
                    details_response = requests.get(details_url, headers=headers)

                    if details_response.status_code == 200:
                        try:
                            details_data = details_response.json()
                            logging.info(f"Details Response: {details_data}")
                            properties = details_data.get('properties', {})
                            return {
                                'Website URL': properties.get('website_url', 'N/A'),
                                'LinkedIn': properties.get('linkedin', 'N/A'),
                                'Short Description': properties.get('short_description', 'N/A')
                            }
                        except requests.exceptions.JSONDecodeError:
                            logging.error("Failed to decode JSON from details response")
                            logging.error(details_response.text)
                    else:
                        logging.error(f"Details request failed with status code {details_response.status_code}")
                else:
                    logging.warning("No entities found in search response")
                break
            except requests.exceptions.JSONDecodeError:
                logging.error("Failed to decode JSON from search response")
                logging.error(search_response.text)
                break
        elif search_response.status_code == 429:
            logging.warning(f"Rate limit exceeded. Retrying in {delay} seconds...")
            time.sleep(delay)
            delay *= 2  # Exponential backoff
        else:
            logging.error(f"Search request failed with status code {search_response.status_code}")
            break

    return {
        'Website URL': 'N/A',
        'LinkedIn': 'N/A',
        'Short Description': 'N/A'
    }


# Streamlit app
def main():
    st.title("Startup Market Map to CSV Converter")
    st.write("Upload an image of a startup market map to convert it into a CSV file.")

    # Get API keys from Streamlit secrets
    api_key = None
    crunchbase_api_key = None

    try:
        api_key = st.secrets["OPENAI_API_KEY"]
    except Exception as e:
        logging.info(f"No OpenAI API key found in secrets: {e}")
        pass

    try:
        crunchbase_api_key = st.secrets["CRUNCHBASE_API_KEY"]
    except Exception as e:
        logging.info(f"No Crunchbase API key found in secrets: {e}")
        pass

    # Add input fields for API keys if not set in secrets
    if not api_key:
        user_api_key = st.text_input("Enter your OpenAI API Key", type="password")
        if user_api_key:
            os.environ["OPENAI_API_KEY"] = user_api_key
            api_key = user_api_key
    
    if not crunchbase_api_key:
        user_crunchbase_key = st.text_input("Enter your Crunchbase API Key", type="password")
        if user_crunchbase_key:
            os.environ["CRUNCHBASE_API_KEY"] = user_crunchbase_key
            crunchbase_api_key = user_crunchbase_key

    uploaded_file = st.file_uploader("Choose an image file", type=["jpg", "jpeg", "png"])

    if uploaded_file is not None:
        image = Image.open(uploaded_file)
        st.image(image, caption='Uploaded Image', use_column_width=True)

        if st.button("Extract Startups"):
            # Check if API keys are available
            if not api_key:
                st.error("OpenAI API key is required.")
                return
            
            if not crunchbase_api_key:
                st.error("Crunchbase API key is required.")
                return
                
            result = get_csv_from_image(image, api_key)

            # Try to extract the CSV content from the response
            try:
                csv_content = result['choices'][0]['message']['content']
                startup_lines = csv_content.strip().split('\n')

                # Filter out unwanted lines and ensure valid startup names
                startups = [line.split(',')[1].strip() for line in startup_lines if
                            ',' in line and len(line.split(',')) == 2 and not any(
                                word in line.lower() for word in ("here is", "startup", "categorized"))]

                enriched_data = []
                for name in startups:
                    crunchbase_data = get_crunchbase_data(name, crunchbase_api_key)
                    enriched_data.append({
                        'Startup Name': name,
                        'Website URL': crunchbase_data['Website URL'],
                        'LinkedIn': crunchbase_data['LinkedIn'],
                        'Short Description': crunchbase_data['Short Description']
                    })

                df = pd.DataFrame(enriched_data)
                csv = df.to_csv(index=False).encode('utf-8')

                st.download_button(
                    label="Download Enriched CSV",
                    data=csv,
                    file_name='enriched_market_map.csv',
                    mime='text/csv'
                )
            except KeyError as e:
                st.error(f"Error extracting CSV content: {e}")
                st.json(result)  # Display the full JSON response for debugging


if __name__ == "__main__":
    main()
