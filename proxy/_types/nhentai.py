from typing import List, NotRequired, TypedDict


class _GalleryImageInfo(TypedDict):
    """Type definition for gallery image data."""

    t: str
    w: NotRequired[int]
    h: NotRequired[int]


GalleryPage = _GalleryImageInfo
GalleryCover = _GalleryImageInfo
GalleryThumbnail = _GalleryImageInfo


class GalleryImage(TypedDict):
    """Type definition for gallery image data."""

    pages: List[GalleryPage]
    cover: GalleryCover
    thumbnail: GalleryThumbnail


class ParsedMangaTitle(TypedDict):
    main_title: str
    chapter_number: int
    chapter_title: str
    english_title: str | None


class _NhentaiTitleData(TypedDict):
    """Type definition for nhentai title data from JSON."""

    english: str | None
    japanese: str | None
    pretty: str


class _NhentaiTagData(TypedDict):
    """Type definition for nhentai tag data from JSON."""

    id: int
    type: str
    name: str
    url: str
    count: int


class _NhentaiData(TypedDict):
    """Type definition for nhentai gallery data from JSON."""

    id: int
    media_id: str
    scanlator: str
    upload_date: int
    num_pages: int
    num_favorites: int
    images: GalleryImage


class NhentaiGalleryData(_NhentaiData):
    """Type definition for nhentai gallery data from JSON."""

    title: _NhentaiTitleData
    tags: List[_NhentaiTagData]


class NhentaiGallery(_NhentaiData):
    """Type definition for nhentai gallery data with additional fields."""

    title: ParsedMangaTitle
    tags: List[str]
    artists: List[str]
    writers: List[str]
    parodies: List[str]
    characters: List[str]
    language: str
    category: str
    page_count: int
    translated: bool
