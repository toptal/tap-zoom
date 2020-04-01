import re

import singer
from singer import metrics, metadata, Transformer
from singer.bookmarks import set_currently_syncing

from tap_zoom.discover import discover
from tap_zoom.endpoints import ENDPOINTS_CONFIG

LOGGER = singer.get_logger()

def get_bookmark(state, stream_name, default):
    return state.get('bookmarks', {}).get(stream_name, default)

def write_bookmark(state, stream_name, value):
    if 'bookmarks' not in state:
        state['bookmarks'] = {}
    state['bookmarks'][stream_name] = value
    singer.write_state(state)

def write_schema(stream):
    schema = stream.schema.to_dict()
    singer.write_schema(stream.tap_stream_id, schema, stream.key_properties)

def sync_endpoint(client, catalog, state, selected_streams, stream_name, endpoint, key_bag):
    persist = endpoint.get('persist', True)

    if persist:
        stream = catalog.get_stream(stream_name)
        schema = stream.schema.to_dict()
        mdata = metadata.to_map(stream.metadata)
        write_schema(stream)

    path = endpoint['path'].format(**key_bag)

    page_size = 1000
    page_number = 1
    while True:
        params = {
            'page_size': page_size,
            'page_number': page_number
        }

        data = client.get(path, params=params, endpoint=stream_name)

        if data is None:
            return

        if 'data_key' in endpoint:
            records = data[endpoint['data_key']]
        else:
            records = [data]

        with metrics.record_counter(stream_name) as counter:
            with Transformer() as transformer:
                for record in records:
                    if persist and stream_name in selected_streams:
                        record_typed = transformer.transform(record,
                                                             schema,
                                                             mdata)
                        singer.write_record(stream_name, record_typed)
                        counter.increment()
                    if 'children' in endpoint:
                        child_key_bag = dict(key_bag)
                        if 'provides' in endpoint:
                            for dest_key, obj_key in endpoint['provides'].items():
                                child_key_bag[dest_key] = record[obj_key]
                        for child_stream_name, child_endpoint in endpoint['children'].items():
                            sync_endpoint(client,
                                          catalog,
                                          state,
                                          selected_streams,
                                          child_stream_name,
                                          child_endpoint,
                                          child_key_bag)

        if endpoint.get('paginate', True) and page_number < data.get('page_count', 1):
            # each endpoint has a different max page size, the server will send the one that is forced
            page_size = data['page_size']
            page_number += 1
        else:
            break

def update_current_stream(state, stream_name=None):  
    set_currently_syncing(state, stream_name) 
    singer.write_state(state)

def sync(client, catalog, state):
    if not catalog:
        catalog = discover()
        selected_streams = catalog.streams
    else:
        selected_streams = catalog.get_selected_streams(state)

    selected_stream_names = []
    for selected_stream in selected_streams:
        selected_stream_names.append(selected_stream.tap_stream_id)

    ## TODO: handle persist dependency

    for stream_name, endpoint in ENDPOINTS_CONFIG.items():
        update_current_stream(state, stream_name)
        sync_endpoint(client,
                      catalog,
                      state,
                      selected_stream_names,
                      stream_name,
                      endpoint,
                      {})

    update_current_stream(state)
