import shortuuid
import os

from django.db.models import ImageField as DjangoImageField

from thumbnails import compat

from .backends import metadata, storage
from .backends.metadata import ImageMeta
from .files import ThumbnailedImageFile
from . import processors, post_processors


class ImageField(DjangoImageField):
    attr_class = ThumbnailedImageFile

    def __init__(self, *args, **kwargs):
        self.resize_source_to = kwargs.pop('resize_source_to', None)
        if kwargs.get('storage'):
            raise ValueError('Please define storage backend in settings.py instead on the field itself')
        kwargs['storage'] = storage.get_backend()
        self.metadata_backend = metadata.get_backend()
        super(ImageField, self).__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super(ImageField, self).deconstruct()
        del kwargs['storage']
        return name, path, args, kwargs

    def __unicode__(self):
        return self.attname

    def pre_save(self, model_instance, add):
        """
        Process the source image through the defined processors.
        """
        file = getattr(model_instance, self.attname)

        if file and not file._committed:
            image_file = file
            if self.resize_source_to:
                file.seek(0)
                image_file = processors.process(file, self.resize_source_to)
                image_file = post_processors.process(image_file, self.resize_source_to)
            filename = str(shortuuid.uuid()) + os.path.splitext(file.name)[1]
            file.save(filename, image_file, save=False)
        return file

    def south_field_triple(self):
        """
        Return a suitable description of this field for South.
        Taken from smiley chris' easy_thumbnails
        """
        from south.modelsinspector import introspector
        field_class = 'django.db.models.fields.files.ImageField'
        args, kwargs = introspector(self)
        return (field_class, args, kwargs)


def fetch(thumbnails, sizes=None):
    """
    Regenerate EXISTING thumbnails, so we don't need to call redis when using
    thumbnails.get() or thumbnails.all(). Currently only support redis backend.
    NotImeplementedError will be raised, if backend is not supported
    """
    # NOTE: This is just working for redis based backend and same backend
    # different backend among thumbnails may results in bugs
    if not thumbnails:
        return

    backend = thumbnails[0].metadata_backend
    try:
        pipeline = backend.redis.pipeline()
    except AttributeError:
        raise NotImplementedError('Only Redis metadata backend is implemented')

    for thumbnail in thumbnails:
        key = thumbnail.metadata_backend.get_thumbnail_key(thumbnail.source_image.name)

        if sizes:
            pipeline.hmget(key, sizes)
        else:
            pipeline.hgetall(key)

    # if sizes is provided results will be list of lists, else it will be list of dicts
    results = pipeline.execute()
    for thumbnail, data in zip(thumbnails, results):
        source_name = thumbnail.source_image.name
        thumbnail._thumbnails = {}

        if sizes:
            # data shold be list, thus group it with its size beforehand
            items = zip(sizes, data)
        else:
            # data should be dict
            items = data.items()

        for size, name in items:
            thumbnail._thumbnails[compat.as_text(size)] = ImageMeta(source_name, name, size)