from django.core.cache import cache
from utils.logger import logger

def add_item_to_existing_cache(cache_key, new_value):
    """
    Safely add an item to an existing cache list.
    Works for all cache backends (Redis, LocMem, Memcached).
    """
    try:
        items = cache.get(cache_key)

        # If cache key doesn't exist or is not a list
        if not isinstance(items, list):
            items = []

        # Append safely
        items.append(new_value)
        # Save back
        cache.set(cache_key, items, timeout=None)

        logger.info(f"Item added to cache {cache_key}: {new_value}")
        return True
        
    except Exception as e:
        logger.error(f"Error adding item to cache {cache_key}: {e}")
        return False

def delete_item_from_existing_cache(cache_key, removed_id):
    '''
    Delete an item from an existing cache list.
    '''
    try:
        items = cache.get(cache_key, [])
        # keep everything except the item you want removed
        items = [x for x in items if x.get("ID") != int(removed_id)]

        cache.set(cache_key, items)
        logger.info(f"Item removed from cache {cache_key}: {removed_id}")
        return True
    except Exception as e:
        logger.error(f"Error removing item from cache {cache_key}: {e}")
        return False    

def update_item_in_existing_cache(cache_key, updated_id, updated_value):
    """
    Safely update an item in an existing cache list using index assignment.
    This avoids update issues caused by immutability or shallow copying.
    """
    try:
        items = cache.get(cache_key, [])

        for idx, item in enumerate(items):
            # make sure item is a dict
            if isinstance(item, dict) and item.get("ID") == int(updated_id):
                
                # replace with merged dictionary
                updated_item = {**item, **updated_value}
                items[idx] = updated_item
                
                cache.set(cache_key, items)                
                logger.info(f"Item updated in cache {cache_key}: {updated_id}")
                return True
        logger.info(f"Item not found in cache {cache_key}: {updated_id}")        
        return False

    except Exception as e:
        logger.error(f"Error updating item in cache {cache_key}: {e}")
        return False