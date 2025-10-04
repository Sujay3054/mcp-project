# production_notional_mcp.py
import os
import re
import asyncio
import logging
from typing import Optional, Dict, Any, List
from notion_client import Client
from mcp.server.fastmcp import FastMCP

# ---------------- CONFIG ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("notion_mcp")

mcp = FastMCP("notion-mcp")

# Use environment variable in production.
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
if not NOTION_TOKEN:
    raise RuntimeError("❌ Please set NOTION_TOKEN in environment variables.")

notion = Client(auth=NOTION_TOKEN)

# ---------------- HELPERS ----------------
_UUID_RE = re.compile(r"^[0-9a-fA-F-]{32,36}$")


def validate_notion_id(notion_id: str) -> bool:
    if not notion_id or not isinstance(notion_id, str):
        return False
    return bool(_UUID_RE.match(notion_id))


def _func_name(func) -> str:
    # Some Notion client endpoints are bound objects without __name__
    return getattr(func, "__name__", getattr(func, "__qualname__", func.__class__.__name__))


def safe_execute(func, *args, **kwargs):
    """
    Calls Notion client endpoint or a function and returns structured JSON.
    Works when `func` is a bound endpoint object (no __name__).
    """
    try:
        data = func(*args, **kwargs)
        logger.info("✅ Success calling %s", _func_name(func))
        return {"successful": True, "data": data, "error": None}
    except Exception as e:
        logger.exception("❌ Error calling %s", _func_name(func))
        return {"successful": False, "data": {}, "error": str(e)}


def _collect_all_pages_query(database_id: str, page_size: int = 100) -> Dict[str, Any]:
    """
    Helper to collect all pages from databases.query with pagination.
    Returns dict with results list and next_cursor (None if done).
    """
    all_results = []
    start_cursor = None
    while True:
        kwargs = {"database_id": database_id, "page_size": page_size}
        if start_cursor:
            kwargs["start_cursor"] = start_cursor
        res = safe_execute(lambda **kw: notion.databases.query(**kw), **kwargs)
        if not res["successful"]:
            return res
        page_data = res["data"]
        all_results.extend(page_data.get("results", []))
        start_cursor = page_data.get("next_cursor")
        if not start_cursor:
            break
    return {"successful": True, "data": {"results": all_results}, "error": None}


def _collect_all_blocks(block_id: str, page_size: int = 100) -> Dict[str, Any]:
    """Collect all children blocks for a block/page with pagination."""
    all_results = []
    start_cursor = None
    while True:
        kwargs = {"block_id": block_id, "page_size": page_size}
        if start_cursor:
            kwargs["start_cursor"] = start_cursor
        res = safe_execute(lambda **kw: notion.blocks.children.list(**kw), **kwargs)
        if not res["successful"]:
            return res
        all_results.extend(res["data"].get("results", []))
        start_cursor = res["data"].get("next_cursor")
        if not start_cursor:
            break
    return {"successful": True, "data": {"results": all_results}, "error": None}


# ---------------- USER TOOLS ----------------
@mcp.tool()
def NOTION_GET_ABOUT_ME():
    """Retrieves the bot object for the authenticated Notion integration token.

    This provides basic information about the bot's identity.

    Returns:
        The full Notion bot user object.
    """
    return safe_execute(lambda **kw: notion.users.me(**kw))


@mcp.tool()
def NOTION_LIST_USERS(page_size: int = 30, start_cursor: Optional[str] = None):
    """Lists all users in the workspace, including human users and bots.

    This tool supports pagination for workspaces with many users. Use the
    `start_cursor` from a previous response to fetch the next page.

    Args:
        page_size (int, optional): The maximum number of users to return in one request. Defaults to 30.
        start_cursor (str, optional): The cursor for fetching the next page of results.

    Returns:
        A dictionary containing a simplified list of user objects, each with an 'id' and a 'name'.
    """
    kwargs = {"page_size": page_size}
    if start_cursor:
        kwargs["start_cursor"] = start_cursor
    res = safe_execute(lambda **kw: notion.users.list(**kw), **kwargs)
    if res["successful"]:
        simplified = [{"id": u.get("id"), "name": u.get("name", "Unknown")} for u in res["data"].get("results", [])]
        res["data"] = simplified
    return res


@mcp.tool()
def NOTION_GET_ABOUT_USER(user_id: str):
    """Retrieves detailed information for a specific user ID.

    Args:
        user_id (str): The ID of the user to retrieve (e.g., "12345678-abcd-1234-efgh-1234567890ab").

    Returns:
        The full Notion user object for the specified ID.
    """
    if not validate_notion_id(user_id):
        return {"successful": False, "data": {}, "error": "Invalid user ID format"}
    return safe_execute(lambda **kw: notion.users.retrieve(**kw), user_id=user_id)


# ---------------- PAGE / DUPLICATE / UPDATE TOOLS ----------------
@mcp.tool()
def NOTION_CREATE_NOTION_PAGE(parent_id: str, title: str, cover: Optional[str] = None, icon: Optional[str] = None):
    """Creates a new page under a specified parent (page or database).

    Optionally sets a cover URL and an emoji icon for the new page.

    Args:
        parent_id (str): The ID of the parent page or database for the new page.
        title (str): The title of the new page.
        cover (str, optional): A URL for an external cover image.
        icon (str, optional): An emoji to use as the page icon (e.g., "🎉").

    Returns:
        The full Notion page object that was created.
    """
    if not validate_notion_id(parent_id):
        return {"successful": False, "data": {}, "error": "Invalid parent ID format"}
    kwargs = {
        "parent": {"page_id": parent_id},
        "properties": {"title": {"title": [{"text": {"content": title}}]}},
    }
    if cover:
        kwargs["cover"] = {"external": {"url": cover}}
    if icon:
        kwargs["icon"] = {"emoji": icon}
    return safe_execute(lambda **kw: notion.pages.create(**kw), **kwargs)


@mcp.tool()
def NOTION_UPDATE_PAGE(page_id: str, title: Optional[str] = None, archived: Optional[bool] = None, cover_url: Optional[str] = None, icon_emoji: Optional[str] = None, properties: Optional[Dict[str, Any]] = None):
    """Updates a page's metadata and properties.

    Can be used to change the title, icon, cover, archive status, or any other
    property of a page.

    Args:
        page_id (str): The ID of the page to update.
        title (str, optional): The new title for the page.
        archived (bool, optional): Set to true to archive, or false to restore.
        cover_url (str, optional): A URL for a new external cover image.
        icon_emoji (str, optional): An emoji for the new page icon.
        properties (Dict[str, Any], optional): A dictionary of page properties to update.

    Returns:
        The full, updated Notion page object.
    """
    if not validate_notion_id(page_id):
        return {"successful": False, "data": {}, "error": "Invalid page_id format"}
    kwargs = {}
    if archived is not None:
        kwargs["archived"] = archived
    if cover_url:
        kwargs["cover"] = {"external": {"url": cover_url}}
    if icon_emoji:
        kwargs["icon"] = {"emoji": icon_emoji}
    if title:
        kwargs.setdefault("properties", {})
        page_meta = safe_execute(lambda **kw: notion.pages.retrieve(**kw), page_id=page_id)
        if page_meta["successful"]:
            page_props = page_meta["data"].get("properties", {})
            title_key = next((k for k, v in page_props.items() if isinstance(v, dict) and "title" in v), "Name")
            kwargs["properties"][title_key] = {"title": [{"text": {"content": title}}]}
        else:
            kwargs["properties"]["Name"] = {"title": [{"text": {"content": title}}]}
    if properties:
        kwargs.setdefault("properties", {})
        kwargs["properties"].update(properties)
    return safe_execute(lambda **kw: notion.pages.update(**kw), page_id=page_id, **kwargs)


@mcp.tool()
def NOTION_GET_PAGE_PROPERTY_ACTION(page_id: str, property_id: str, page_size: Optional[int] = None, start_cursor: Optional[str] = None):
    """Retrieves the value of a single page property.

    Useful for getting a specific field from a database row (page).

    Args:
        page_id (str): The ID of the page.
        property_id (str): The ID of the property to retrieve.
        page_size (int, optional): The number of items to return for paginated properties.
        start_cursor (str, optional): The cursor for fetching the next page of results.

    Returns:
        A Property Item object containing the value of the specified property.
    """
    if not validate_notion_id(page_id):
        return {"successful": False, "data": {}, "error": "Invalid page_id format"}
    kwargs = {"page_id": page_id, "property_id": property_id}
    if page_size:
        kwargs["page_size"] = page_size
    if start_cursor:
        kwargs["start_cursor"] = start_cursor
    return safe_execute(lambda **kw: notion.pages.properties.retrieve(**kw), **kwargs)


@mcp.tool()
def NOTION_ARCHIVE_NOTION_PAGE(page_id: str, archive: bool = True):
    """Moves a page to the trash (archives it) or restores it.

    This is a simplified version of the update_page tool.

    Args:
        page_id (str): The ID of the page to archive or restore.
        archive (bool, optional): Set to true to archive, false to restore. Defaults to true.

    Returns:
        The updated Notion page object.
    """
    if not validate_notion_id(page_id):
        return {"successful": False, "data": {}, "error": "Invalid page_id format"}
    return safe_execute(lambda **kw: notion.pages.update(**kw), page_id=page_id, archived=archive)


@mcp.tool()
def list_pages(keyword: Optional[str] = None):
    """Searches for and lists accessible pages.

    Results can be filtered by a keyword query that searches page titles.

    Args:
        keyword (str, optional): A keyword to filter pages by title.

    Returns:
        A list of simplified page objects, each containing an 'id', 'title', and 'url'.
    """
    search_kwargs = {"filter": {"property": "object", "value": "page"}}
    if keyword:
        search_kwargs["query"] = keyword
    res = safe_execute(lambda **kw: notion.search(**kw), **search_kwargs)
    if not res["successful"]:
        return res
    pages = []
    for pg in res["data"].get("results", []):
        title = "Untitled"
        try:
            props = pg.get("properties", {})
            title_prop = next((v for v in props.values() if isinstance(v, dict) and "title" in v), None)
            if title_prop:
                title = "".join([t.get("plain_text", "") for t in title_prop.get("title", [])]) or title
        except Exception:
            pass
        pages.append({"id": pg.get("id"), "title": title, "url": pg.get("url")})
    return {"successful": True, "data": pages, "error": ""}


# ---------------- DATABASE TOOLS ----------------
@mcp.tool()
def NOTION_CREATE_DATABASE(parent_id: str, title: str, properties: Dict[str, Any]):
    """Creates a new database as a sub-page under a specified parent page.

    The structure of the database columns must be defined in the `properties`
    dictionary, following the Notion API's schema format. At least one property
    must be of the 'title' type.

    Args:
        parent_id (str): The ID of the page that will contain the new database.
        title (str): The title for the new database.
        properties (Dict[str, Any]): A dictionary defining the database schema.

    Returns:
        The full Notion database object that was created.
    """
    if not validate_notion_id(parent_id):
        return {"successful": False, "data": {}, "error": "Invalid parent_id format"}
    if not any(isinstance(v, dict) and "title" in v for v in properties.values()):
        return {"successful": False, "data": {}, "error": "Database must have at least one title property"}
    payload = {"parent": {"type": "page_id", "page_id": parent_id}, "title": [{"type": "text", "text": {"content": title}}], "properties": properties}
    return safe_execute(lambda **kw: notion.databases.create(**kw), **payload)


@mcp.tool()
def NOTION_INSERT_ROW_DATABASE(database_id: str, properties: Dict[str, Any], icon: Optional[str] = None, cover: Optional[str] = None, children: Optional[List[Dict[str, Any]]] = None):
    """Creates a new page (row) inside a specified database.

    Requires a `properties` dictionary with values that match the database schema.

    Args:
        database_id (str): The ID of the target database.
        properties (Dict[str, Any]): A dictionary of property values for the new row.
        icon (str, optional): An emoji icon for the new row's page.
        cover (str, optional): A URL for the new row page's cover image.
        children (List[Dict[str, Any]], optional): A list of block objects to add as content.

    Returns:
        The full Notion page object that was created for the new row.
    """
    if not validate_notion_id(database_id):
        return {"successful": False, "data": {}, "error": "Invalid database_id format"}
    payload = {"parent": {"database_id": database_id}, "properties": properties}
    if icon:
        payload["icon"] = {"emoji": icon}
    if cover:
        payload["cover"] = {"external": {"url": cover}}
    if children:
        payload["children"] = children
    return safe_execute(lambda **kw: notion.pages.create(**kw), **payload)


@mcp.tool()
def NOTION_QUERY_DATABASE(database_id: str, page_size: int = 10, sorts: Optional[List[Dict[str, Any]]] = None, start_cursor: Optional[str] = None):
    """Queries a database to retrieve a list of pages (rows).

    Supports sorting, limiting results, and pagination for large databases.

    Args:
        database_id (str): The ID of the database to query.
        page_size (int, optional): The number of rows to return per request. Defaults to 10.
        sorts (List[Dict[str, Any]], optional): A list of sort objects to order the results.
        start_cursor (str, optional): The cursor for fetching the next page of results.

    Returns:
        A paginated list of Notion page objects (rows) that match the query.
    """
    if not validate_notion_id(database_id):
        return {"successful": False, "data": {}, "error": "Invalid database_id format"}
    payload = {"page_size": page_size}
    if sorts:
        payload["sorts"] = [{"property": s["property"], "direction": s.get("direction", "ascending")} for s in sorts]
    if start_cursor:
        payload["start_cursor"] = start_cursor
    return safe_execute(lambda **kw: notion.databases.query(**kw), database_id=database_id, **payload)


@mcp.tool()
def NOTION_FETCH_DATABASE(database_id: str):
    """Retrieves the full metadata of a specific database.

    This includes its title, schema, properties, and parent.

    Args:
        database_id (str): The ID of the database to fetch.

    Returns:
        The full Notion database object.
    """
    if not validate_notion_id(database_id):
        return {"successful": False, "data": {}, "error": "Invalid database_id format"}
    return safe_execute(lambda **kw: notion.databases.retrieve(**kw), database_id=database_id)


@mcp.tool()
def NOTION_FETCH_ROW(page_id: str):
    """Retrieves the properties and metadata of a page (row) in a database.

    Args:
        page_id (str): The ID of the page (row) to retrieve.

    Returns:
        The full Notion page object with all its properties.
    """
    if not validate_notion_id(page_id):
        return {"successful": False, "data": {}, "error": "Invalid page_id format"}
    return safe_execute(lambda **kw: notion.pages.retrieve(**kw), page_id=page_id)


@mcp.tool()
def NOTION_UPDATE_ROW_DATABASE(page_id: str, properties: Optional[Dict[str, Any]] = None, icon: Optional[str] = None, cover: Optional[str] = None, archived: Optional[bool] = False):
    """Updates the properties of a page (row) within a database.

    Can also be used to update the icon, cover, or archive status of the page.

    Args:
        page_id (str): The ID of the page (row) to update.
        properties (Dict[str, Any], optional): A dictionary of property values to update.
        icon (str, optional): A new emoji icon for the page.
        cover (str, optional): A new cover image URL for the page.
        archived (bool, optional): Set to true to archive the row. Defaults to false.

    Returns:
        The full, updated Notion page object.
    """
    if not validate_notion_id(page_id):
        return {"successful": False, "data": {}, "error": "Invalid page_id format"}
    payload = {}
    if properties:
        payload["properties"] = properties
    if icon:
        payload["icon"] = {"emoji": icon}
    if cover:
        payload["cover"] = {"external": {"url": cover}}
    if archived is not None:
        payload["archived"] = archived
    return safe_execute(lambda **kw: notion.pages.update(**kw), page_id=page_id, **payload)


@mcp.tool()
def NOTION_UPDATE_SCHEMA_DATABASE(database_id: str, title: Optional[str] = None, description: Optional[str] = None, properties: Optional[Dict[str, Any]] = None):
    """Updates the schema, title, or description of an existing database.

    Args:
        database_id (str): The ID of the database to update.
        title (str, optional): A new title for the database.
        description (str, optional): A new description for the database.
        properties (Dict[str, Any], optional): A new properties object to redefine the schema.

    Returns:
        The full, updated Notion database object.
    """
    if not validate_notion_id(database_id):
        return {"successful": False, "data": {}, "error": "Invalid database_id format"}
    payload = {}
    if title:
        payload["title"] = [{"type": "text", "text": {"content": title}}]
    if description:
        payload["description"] = [{"type": "text", "text": {"content": description}}]
    if properties:
        payload["properties"] = properties
    return safe_execute(lambda **kw: notion.databases.update(**kw), database_id=database_id, **payload)


# ---------------- BLOCK TOOLS ----------------
def markdown_to_rich_text(content: str) -> List[Dict[str, Any]]:
    # Minimal conversion: returns single text rich_text item. Extend as needed.
    return [{"type": "text", "text": {"content": content}}]


@mcp.tool()
def NOTION_ADD_MULTIPLE_PAGE_CONTENT(parent_block_id: str, content_blocks: List[Dict[str, Any]], after: Optional[str] = None):
    """Appends a list of content blocks to a page or block.

    A maximum of 100 blocks can be added in a single request.

    Args:
        parent_block_id (str): The ID of the page or block to add content to.
        content_blocks (List[Dict[str, Any]]): A list of valid Notion block objects.
        after (str, optional): The ID of an existing block to append the new content after.

    Returns:
        A list of the newly created block objects.
    """
    if not validate_notion_id(parent_block_id):
        return {"successful": False, "data": {}, "error": "Invalid parent_block_id"}
    if not isinstance(content_blocks, list) or len(content_blocks) == 0:
        return {"successful": False, "data": {}, "error": "content_blocks must be a non-empty list"}
    if len(content_blocks) > 100:
        return {"successful": False, "data": {}, "error": "Maximum 100 blocks per request"}

    parsed_blocks = []
    for block in content_blocks:
        if isinstance(block, dict) and block.get("object") == "block":
            parsed_blocks.append(block)
        elif isinstance(block, dict) and "content" in block:
            parsed_blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": markdown_to_rich_text(block["content"])}})
        else:
            return {"successful": False, "data": {}, "error": f"Invalid block format: {block}"}

    payload = {"children": parsed_blocks}
    if after is not None:
        payload["after"] = after
    return safe_execute(lambda **kw: notion.blocks.children.append(**kw), block_id=parent_block_id, **payload)


@mcp.tool()
def NOTION_ADD_PAGE_CONTENT(parent_block_id: str, content_block: Dict[str, Any], after: Optional[str] = None):
    """Appends a single content block to a page or parent block.

    Args:
        parent_block_id (str): The ID of the page or block to add content to.
        content_block (Dict[str, Any]): A single valid Notion block object.
        after (str, optional): The ID of an existing block to append the new content after.

    Returns:
        The newly created block object.
    """
    if not validate_notion_id(parent_block_id):
        return {"successful": False, "data": {}, "error": "Invalid parent_block_id"}
    if not isinstance(content_block, dict):
        return {"successful": False, "data": {}, "error": "content_block must be an object"}
    payload = {"children": [content_block]}
    if after is not None:
        payload["after"] = after
    return safe_execute(lambda **kw: notion.blocks.children.append(**kw), block_id=parent_block_id, **payload)


@mcp.tool()
def NOTION_APPEND_BLOCK_CHILDREN(block_id: str, children: List[Dict[str, Any]], after: Optional[str] = None):
    """Appends a list of child blocks to a parent block.

    Args:
        block_id (str): The ID of the parent block.
        children (List[Dict[str, Any]]): A list of valid Notion block objects to append.
        after (str, optional): The ID of an existing child block to append the new content after.

    Returns:
        A list of the newly created block objects.
    """
    if not validate_notion_id(block_id):
        return {"successful": False, "data": {}, "error": "Invalid block_id"}
    if not isinstance(children, list) or len(children) == 0:
        return {"successful": False, "data": {}, "error": "children must be a non-empty list"}
    if len(children) > 100:
        return {"successful": False, "data": {}, "error": "Maximum 100 blocks per request"}
    payload = {"children": children}
    if after is not None:
        payload["after"] = after
    return safe_execute(lambda **kw: notion.blocks.children.append(**kw), block_id=block_id, **payload)


@mcp.tool()
def NOTION_UPDATE_BLOCK(block_id: str, block_type: str, content: str, additional_properties: Optional[Dict[str, Any]] = None):
    """Updates the content of an existing block.

    For example, changing paragraph text or toggling a to-do checkbox.

    Args:
        block_id (str): The ID of the block to update.
        block_type (str): The type of the block (e.g., 'paragraph', 'to_do', 'heading_1').
        content (str): The new markdown text content for the block.
        additional_properties (Dict[str, Any], optional): Extra properties, like `checked: true` for a 'to_do' block.

    Returns:
        The full, updated Notion block object.
    """
    if not validate_notion_id(block_id):
        return {"successful": False, "data": {}, "error": "Invalid block_id"}
    block_payload = {}
    if block_type in ("paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item", "numbered_list_item", "quote"):
        block_payload[block_type] = {"rich_text": markdown_to_rich_text(content)}
        if additional_properties:
            block_payload[block_type].update(additional_properties)
    elif block_type == "to_do":
        block_payload["to_do"] = {"rich_text": markdown_to_rich_text(content)}
        if additional_properties:
            block_payload["to_do"].update(additional_properties)
    else:
        if not additional_properties:
            return {"successful": False, "data": {}, "error": f"Unsupported block_type '{block_type}' without additional_properties"}
        block_payload[block_type] = additional_properties
    return safe_execute(lambda **kw: notion.blocks.update(**kw), block_id=block_id, **block_payload)


@mcp.tool()
def NOTION_DELETE_BLOCK(block_id: str):
    """Deletes a block by archiving it (moving it to the trash).

    Args:
        block_id (str): The ID of the block to delete.

    Returns:
        The archived block object.
    """
    if not validate_notion_id(block_id):
        return {"successful": False, "data": {}, "error": "Invalid block_id"}
    return safe_execute(lambda **kw: notion.blocks.update(**kw), block_id=block_id, archived=True)


@mcp.tool()
def NOTION_FETCH_BLOCK_CONTENTS(block_id: str, page_size: Optional[int] = None, start_cursor: Optional[str] = None):
    """Lists the children blocks contained within a parent block or page.

    Supports pagination for blocks with many children.

    Args:
        block_id (str): The ID of the parent block or page.
        page_size (int, optional): The number of blocks to return per request.
        start_cursor (str, optional): The cursor for fetching the next page of results.

    Returns:
        A paginated list of child block objects.
    """
    if not validate_notion_id(block_id):
        return {"successful": False, "data": {}, "error": "Invalid block_id"}
    kwargs = {"block_id": block_id}
    if page_size:
        kwargs["page_size"] = page_size
    if start_cursor:
        kwargs["start_cursor"] = start_cursor
    return safe_execute(lambda **kw: notion.blocks.children.list(**kw), **kwargs)


@mcp.tool()
def NOTION_FETCH_BLOCK_METADATA(block_id: str):
    """Retrieves the metadata for a single block.

    This includes its type, parent, archived status, etc.

    Args:
        block_id (str): The ID of the block to retrieve.

    Returns:
        The full Notion block object.
    """
    if not validate_notion_id(block_id):
        return {"successful": False, "data": {}, "error": "Invalid block_id"}
    return safe_execute(lambda **kw: notion.blocks.retrieve(**kw), block_id=block_id)


# ---------------- COMMENT TOOLS ----------------
@mcp.tool()
def NOTION_CREATE_COMMENT(comment: Dict[str, Any], discussion_id: Optional[str] = None, parent_page_id: Optional[str] = None):
    """Creates a new comment on a page or in an existing discussion.

    Requires either a `discussion_id` (to reply to a thread) or a `parent_page_id`
    (to start a new thread on a page).

    Args:
        comment (Dict[str, Any]): A dictionary containing the comment content, e.g., `{"content": "This is a comment."}`.
        discussion_id (str, optional): The ID of the discussion thread to reply to.
        parent_page_id (str, optional): The ID of the page to start a new comment thread on.

    Returns:
        The full Notion comment object that was created.
    """
    if not discussion_id and not parent_page_id:
        return {"successful": False, "data": {}, "error": "Either discussion_id or parent_page_id must be provided."}
    payload = {"rich_text": [{"type": "text", "text": {"content": comment.get("content", "")}}]}
    if discussion_id:
        payload["discussion_id"] = discussion_id
    else:
        payload["parent"] = {"type": "page_id", "page_id": parent_page_id}
    return safe_execute(lambda **kw: notion.comments.create(**kw), **payload)


@mcp.tool()
def NOTION_GET_COMMENT_BY_ID(parent_block_id: str, comment_id: str):
    """Fetches a specific comment by its ID from a page or block.

    Args:
        parent_block_id (str): The ID of the page or block where the comment exists.
        comment_id (str): The ID of the comment to retrieve.

    Returns:
        The specified Notion comment object if found.
    """
    if not parent_block_id or not comment_id:
        return {"successful": False, "data": {}, "error": "parent_block_id and comment_id are required."}
    kwargs = {"block_id": parent_block_id, "page_size": 100}
    res = safe_execute(lambda **kw: notion.comments.list(**kw), **kwargs)
    if not res["successful"]:
        return res
    for c in res["data"].get("results", []):
        if c.get("id") == comment_id:
            return {"successful": True, "data": c, "error": None}
    return {"successful": False, "data": {}, "error": f"Comment with ID {comment_id} not found."}


@mcp.tool()
def NOTION_FETCH_COMMENTS(block_id: str, page_size: Optional[int] = 100, start_cursor: Optional[str] = None):
    """Lists all comments on a page or under a specific block.

    Supports pagination for pages with many comments.

    Args:
        block_id (str): The ID of the page or block to fetch comments from.
        page_size (int, optional): The number of comments to return per request. Defaults to 100.
        start_cursor (str, optional): The cursor for fetching the next page of results.

    Returns:
        A paginated list of Notion comment objects.
    """
    if not block_id:
        return {"successful": False, "data": {}, "error": "block_id is required."}
    kwargs = {"block_id": block_id, "page_size": page_size}
    if start_cursor is not None:
        kwargs["start_cursor"] = start_cursor
    return safe_execute(lambda **kw: notion.comments.list(**kw), **kwargs)


# ---------------- SEARCH TOOLS ----------------
@mcp.tool()
def NOTION_SEARCH_NOTION_PAGE(query: Optional[str] = "", page_size: int = 10, start_cursor: Optional[str] = None):
    """Searches Notion pages and databases by title.

    An empty query returns all accessible items. Supports sorting and filtering.

    Args:
        query (str, optional): The search term. Defaults to an empty string.
        page_size (int, optional): The number of results to return per request. Defaults to 10.
        start_cursor (str, optional): The cursor for fetching the next page of results.

    Returns:
        A list of Notion page or database objects that match the search criteria.
    """
    kwargs = {"page_size": page_size, "query": query}
    if start_cursor:
        kwargs["start_cursor"] = start_cursor
    return safe_execute(lambda **kw: notion.search(**kw), **kwargs)


@mcp.tool()
def NOTION_FETCH_DATA(get_all: bool = False, get_databases: bool = False, get_pages: bool = False, page_size: int = 100, query: Optional[str] = None):
    """Fetches Notion items (pages and/or databases) based on filters.

    If no filter is set, this tool defaults to fetching pages.

    Args:
        get_all (bool, optional): Set to true to fetch both pages and databases.
        get_databases (bool, optional): Set to true to fetch only databases.
        get_pages (bool, optional): Set to true to fetch only pages.
        page_size (int, optional): The number of items to return per request. Defaults to 100.
        query (str, optional): A keyword to filter the results.

    Returns:
        A list of Notion page and/or database objects matching the filters.
    """
    kwargs = {"page_size": page_size}
    if query:
        kwargs["query"] = query
    if get_all:
        return safe_execute(lambda **kw: notion.search(**kw), **kwargs)
    if get_databases:
        kwargs["filter"] = {"property": "object", "value": "database"}
    else:  # Default to pages
        kwargs["filter"] = {"property": "object", "value": "page"}
    return safe_execute(lambda **kw: notion.search(**kw), **kwargs)

# ---------------- ENTRYPOINT ----------------
if __name__ == "__main__":
    logger.info("Starting Notion MCP server...")
    asyncio.run(mcp.run())
