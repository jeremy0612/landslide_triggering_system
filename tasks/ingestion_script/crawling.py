import openmeteo_requests
import requests_cache
import pandas as pd
import argparse
import pycountry
from retry_requests import retry
from datetime import datetime, timedelta
from restcountries import RestCountryApiV2 as rapi

parser = argparse.ArgumentParser()
parser.add_argument('--source', type=str, default='csv', help='Source of the data (csv or excel)')
parser.add_argument('--mode', type=str, default='ingest', help='Working mode for ingestion or prediction pipeline')
args = parser.parse_args()

def fetch_country_code(name):
    try:
        code = pycountry.countries.get(name=name).alpha_3
        return code
    except:
        exception = {
            "Democratic Republic of Congo": "COD",
            "Myanmar (Burma)": "MMR",
            "North Korea": "PRK",
            "South Korea": "KOR",
            "The Republic of Korea": "KOR",
            "St. Lucia": "LCA"
        }
        try:
            country_list = rapi.get_countries_by_name(name)
        except:
            try:
                return exception[name]
            except:
                return 'Unknown'
        country = country_list[0]
        return country.alpha3_code
        
class Crawler:
    def __init__(self):
        # Initialize API client with cache and retry
        self.cache_session = requests_cache.CachedSession('.cache', expire_after=-1)
        self.retry_session = retry(self.cache_session, retries=5, backoff_factor=0.2)
        self.openmeteo = openmeteo_requests.Client(session=self.retry_session)
        self.hourly_variables = ["temperature_2m", "relative_humidity_2m", "precipitation", "rain", "soil_temperature_0_to_7cm", "soil_temperature_7_to_28cm", "soil_temperature_28_to_100cm", "soil_temperature_100_to_255cm", "soil_moisture_0_to_7cm", "soil_moisture_7_to_28cm", "soil_moisture_28_to_100cm", "soil_moisture_100_to_255cm"]

    def fetch_weather_data(self, params):
        url = "https://archive-api.open-meteo.com/v1/archive"
        try:
            params = {
                "latitude": params['latitude'],
                "longitude": params['longitude'],
                "start_date": params['start_date'],
                "end_date": params['end_date'],
                "hourly": self.hourly_variables
            }
        except:
            params = {
                "latitude": params['latitude'],
                "longitude": params['longitude']
            }
        responses = self.openmeteo.weather_api(url, params=params)
        return responses[0]  # Handling only the first location

    def process_response(self, response):
        # print(f"Coordinates {response.Latitude()}°N {response.Longitude()}°E")
        # print(f"Elevation {response.Elevation()} m asl")
        # print(f"Timezone {response.Timezone()} {response.TimezoneAbbreviation()}")
        # print(f"Timezone difference to GMT+0 {response.UtcOffsetSeconds()} s")

        hourly = response.Hourly()
        hourly_data = {
            "date": pd.date_range(
                start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
                end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
                freq=pd.Timedelta(seconds=hourly.Interval()),
                inclusive="left"
            ),
            "elevation": response.Elevation()
        }

        for i, variable in enumerate(self.hourly_variables):
            hourly_data[variable] = hourly.Variables(i).ValuesAsNumpy()
        return pd.DataFrame(data=hourly_data)
    
    def fetch_data(self,params):
        hourly_dataframe = self.process_response(self.fetch_weather_data(params=params))
        return hourly_dataframe
    
if __name__ == "__main__":
    crawler = Crawler()
    try:
        if args.mode == 'ingest':
            if args.source == 'csv':
                occur = pd.read_json('/app/buffer/origin/date_csv_modified.json')
                out = "/app/buffer/origin/metrics_csv.csv"
            else:
                occur = pd.read_json('/app/buffer/origin/date_xlsx_modified.json')
                out = "/app/buffer/origin/metrics_xlsx.csv"
        else:
            occur = pd.read_json('/app/buffer/message_broker/outline.json')
            out = "/app/buffer/message_broker/outline_data.csv"

        for line in occur.itertuples():
            info = {
                    'latitude': line.latitude,
                    'longitude': line.longitude,
                    'start_date': "-".join([str(line.year), str(line.month).zfill(2), str(line.date).zfill(2)]),
                    'end_date': "-".join([str(line.year), str(line.month).zfill(2), str(line.date).zfill(2)])
                }
            response = crawler.fetch_data(info)
            if isinstance(response, pd.DataFrame):
                df = pd.DataFrame([line])
                new_row = df.loc[0].copy()
                df = df._append([new_row]*23, ignore_index=True)
                # Combine information with response DataFrame efficiently
                combined_df = pd.concat([df, response.rename(columns={'date': 'datetime'})], axis=1)
                # Handle potential column name conflicts:
                if any(col in combined_df.columns for col in info.keys()):
                    combined_df.columns = [f'{col}_info' if col in info.keys() else col for col in combined_df.columns]
                # countries = {}
                # for country in pycountry.countries:
                #     countries[country.name] = country.alpha_3
                # combined_df['country_code'] = [countries.get(country, 'Unknown code') for country in combined_df['country']]
                combined_df['country_code'] = combined_df['country'].apply(fetch_country_code)
                # print(combined_df)
            else:
                print(f"Warning: Unexpected response type from crawler.fetch_data({info}). Expected pandas.DataFrame")
            if line.Index == 0:
                combined_df.to_csv(out, index=False)
            else:
                combined_df.to_csv(out, index=False,mode='a',header=False)

    except Exception as e:
        print(e)

    