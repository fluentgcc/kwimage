"""
Structure for efficient encoding of per-annotation segmentation masks
Inspired by the cocoapi.

THIS IS CURRENTLY A WORK IN PROGRESS.

References:
    https://github.com/nightrome/cocostuffapi/blob/master/PythonAPI/pycocotools/mask.py
    https://github.com/nightrome/cocostuffapi/blob/master/PythonAPI/pycocotools/_mask.pyx
    https://github.com/nightrome/cocostuffapi/blob/master/common/maskApi.c
    https://github.com/nightrome/cocostuffapi/blob/master/common/maskApi.h


Goals:
    The goal of this file is to create a datastructure that lets the developer
    seemlessly convert between:
        (1) raw binary uint8 masks
        (2) memory-efficient comprsssed run-length-encodings of binary
        segmentation masks.
        (3) convex polygons
        (4) convex hull polygons
        (5) bounding box

    It is not there yet, and the API is subject to change in order to better
    accomplish these goals.
"""
import six
import numpy as np
import ubelt as ub

__ignore__ = True  # currently this file is a WIP


class Mask(ub.NiceRepr):
    """ Manages a single segmentation mask """
    def __init__(self, data=None, format=None):
        self.data = data
        self.format = format

    def __nice__(self):
        return '{}, format={}'.format(self.data, self.format)

    @classmethod
    def from_mask(Mask, mask):
        return Masks.from_masks([mask])[0]

    @classmethod
    def from_coco_segmentation(Mask, segmentation):
        self = Masks.from_coco_segmentations([segmentation])[0]
        return self

    @classmethod
    def from_polygon(Mask, polygon, shape):
        self = Masks.from_polygons([polygon], shape)[0]
        return self

    @classmethod
    def union(cls, *others):
        raise NotImplementedError

    @classmethod
    def intersection(cls, *others):
        raise NotImplementedError

    @property
    def mask(self):
        return Masks([self.data], self.format).mask[0]

    def to_polygons(self):
        """
        Returns a list of (x,y)-coordinate lists. The length of the outer list
        is equal to the number of disjoint regions in the mask.
        """
        return Masks([self.data], self.format).to_polygons()[0]

    def draw_on(self, image, color='blue', alpha=0.5):
        """
        Draws the mask on an image

        Example:
            >>> from kwimage.structs.masks import *  # NOQA
            >>> import kwimage
            >>> image = kwimage.grab_test_image()
            >>> self = Mask.demo(shape=image.shape[0:2])
            >>> toshow = self.draw_on(image)
            >>> # xdoc: +REQUIRE(--show)
            >>> import kwplot
            >>> kwplot.autompl()
            >>> kwplot.imshow(toshow)
            >>> kwplot.show_if_requested()
        """
        import kwplot
        import kwimage

        mask = self.mask
        rgb01 = list(kwplot.Color(color).as01())
        rgba01 = np.array(rgb01 + [1])[None, None, :]
        alpha_mask = rgba01 * mask[:, :, None]
        alpha_mask[..., 3] = mask * alpha

        toshow = kwimage.overlay_alpha_images(alpha_mask, image)
        return toshow

    @classmethod
    def demo(Mask, rng=0, shape=(32, 32)):
        import kwarray
        rng = kwarray.ensure_rng(rng)
        mask = (rng.rand(*shape) > .5).astype(np.uint8)
        self = Mask.from_mask(mask)
        return self

    @classmethod
    def coerce(Masks, data):
        """
        Attempts to auto-inspect the format of the data
        """
        from kwimage.structs._mask_backend import cython_mask
        if isinstance(data, np.ndarray):
            if data.flags['F_CONTIGUOUS']:
                self = Mask(data, 'fortran_mask')
            else:
                self = Mask(data, 'mask')
            self = self.to_compressed_rle()
        elif isinstance(data, dict):
            # Handle COCO RLE dictionaries
            if isinstance(data['counts'], six.string_types):
                self = Mask(data, 'compressed_rle')
            else:
                w, h = data['size']
                newdata = cython_mask.frUncompressedRLE([data], h, w)[0]
                self = Mask(newdata, 'compressed_rle')
        elif isinstance(data, list):
            # Polygon
            if len(data) and isinstance(data, (np.ndarray, list)):
                flat_polys = data
            else:
                flat_polys = [data]
            flat_polys = [np.array(p).ravel() for p in flat_polys]
            newdatas = cython_mask.frPoly(flat_polys, h, w)
            newdata = cython_mask.merge(newdatas, intersect=0)
            self = Masks(newdata, 'compressed_rle')
        return self

    def to_compressed_rle(self):
        return Masks([self.data], self.format).to_compressed_rle()


class _MaskConversionMixins(object):

    def to_compressed_rle(self):
        if self.format == 'compressed_rle':
            return self
        elif self.format == 'fortran_mask':
            from kwimage.structs._mask_backend import cython_mask
            fortran_masks = self.fortran_masks
            encoded = cython_mask.encode(fortran_masks)
            self = Masks(encoded, format='compressed_rle')
        elif self.format == 'mask':
            from kwimage.structs._mask_backend import cython_mask
            masks = np.array(self.data)
            fortran_masks = np.asfortranarray(np.transpose(masks, [1, 2, 0]))
            encoded = cython_mask.encode(fortran_masks)
            self = Masks(encoded, format='compressed_rle')
        else:
            raise NotImplementedError(self.format)
        return self


class Masks(ub.NiceRepr, _MaskConversionMixins):
    """
    Manages segmentation multiple masks within the same image

    Python object interface to the C++ backend for encoding binary segmentation
    masks

    WIP:
        >>> from kwimage.structs.masks import *  # NOQA
        >>> self = Masks.demo()
        >>> print(self.data)
        >>> self.union().mask
        >>> self.intersection().mask
        >>> polys = self.to_polygons()
        >>> shape = self.shape
        >>> polygon = polys[0][0]
        >>> #Mask.from_polygon(polygon, shape)

    """

    def __init__(self, data=None, format=None):
        self.data = data
        self.format = format

    @classmethod
    def coerce(Masks, data):
        """
        Attempts to auto-inspect the format of the data
        """
        if len(data) == 0:
            raise ValueError('cannot coerce empty data')

        newdatas = []
        for item in data:
            newitem = Mask.coerce(item).to_compressed_rle().data
            newdatas.append(newitem)
        self = Masks(newdatas, 'compressed_rle')
        return self

    @property
    def shape(self):
        if self.format == 'compressed_rle':
            return self.data[0]['size'][::-1]
        elif self.format == 'uncompressed_rle':
            return self.data[0]['size'][::-1]
        elif self.format == 'mask':
            return self.data[0].shape
        else:
            self.to_compressed_rle().shape

    def __getitem__(self, index):
        return Mask(self.data[index], self.format)

    def __len__(self):
        return len(self.data)

    def __nice__(self):
        return 'n={}, format={}'.format(len(self.data), self.format)

    def union(self):
        from kwimage.structs._mask_backend import cython_mask
        self = self.to_compressed_rle()
        return Mask(cython_mask.merge(self.data, intersect=0), self.format)

    def intersection(self):
        from kwimage.structs._mask_backend import cython_mask
        self = self.to_compressed_rle()
        return Mask(cython_mask.merge(self.data, intersect=1), self.format)

    @property
    def area(self):
        from kwimage.structs._mask_backend import cython_mask
        self = self.to_compressed_rle()
        return cython_mask.area(self.data)

    def to_polygons(self):
        """
        References:
            https://github.com/jsbroks/imantics/blob/master/imantics/annotation.py

        Returns:
            List[List[ndarray]]: the outer list loops over every mask.
                The inner list is a polygon around each connected component of
                the mask.
        """
        import cv2
        polygons = []
        for mask in self.mask:
            padded_mask = cv2.copyMakeBorder(mask, 1, 1, 1, 1,
                                             cv2.BORDER_CONSTANT, value=0)
            contours_, hierarchy_ = cv2.findContours(
                padded_mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE,
                offset=(-1, -1))
            contours = [c[:, 0, :] for c in contours_]
            polygons.append(contours)
        return polygons

    @property
    def boxes(self):
        from kwimage.structs._mask_backend import cython_mask
        import kwimage
        self = self.to_compressed_rle()
        xywh = cython_mask.toBbox(self.data)
        boxes = kwimage.Boxes(xywh, 'xywh')
        return boxes

    @property
    def mask(self):
        from kwimage.structs._mask_backend import cython_mask
        self = self.to_compressed_rle()
        fortran_masks = cython_mask.decode(self.data)
        masks = np.transpose(fortran_masks, [2, 0, 1])
        return masks

    @property
    def fortran_mask(self):
        from kwimage.structs._mask_backend import cython_mask
        self = self.to_compressed_rle()
        fortran_masks = cython_mask.decode(self.data)
        return fortran_masks

    @classmethod
    def from_masks(Masks, masks):
        """
        Args:
            masks (ndarray): [NxHxW] uint8 binary masks

        Example:
            >>> # create 32 random binary masks
            >>> rng = np.random.RandomState(0)
            >>> masks = (rng.rand(10, 32, 32) > .5).astype(np.uint8)
            >>> self = Masks.from_masks(masks)
            >>> assert np.all(masks == self.mask)
        """
        self = Masks(masks, 'mask')
        return self

    @classmethod
    def from_coco_segmentations(Masks, segmentations):
        """
        Example:
            >>> segmentations = [{'size': [5, 9], 'counts': ';?1B10O30O4'},
            >>>                  {'size': [5, 9], 'counts': ';23000c0'},
            >>>                  {'size': [5, 9], 'counts': ';>3C072'}]
        """
        from kwimage.structs._mask_backend import cython_mask
        encoded = []
        for item in segmentations:
            if isinstance(item['counts'], six.string_types):
                encoded.append(item)
            else:
                w, h = item['size']
                newitem = cython_mask.frUncompressedRLE([item], h, w)[0]
                encoded.append(newitem)
        self = Masks(encoded, 'compressed_rle')
        return self

    @classmethod
    def from_polygons(Mask, polygons, shape):
        """
        Example:
            >>> polygons = [
            >>>     [np.array([[3, 0],[2, 1],[2, 4],[4, 4],[4, 3],[7, 0]])],
            >>>     [np.array([[2, 1],[2, 2],[4, 2],[4, 1]])],
            >>> ]
            >>> shape = (9, 5)
        """
        raise NotImplementedError('todo handle disjoint cases')
        from kwimage.structs._mask_backend import cython_mask
        h, w = shape
        flat_polys = [ps[0].ravel() for ps in polygons]
        encoded = cython_mask.frPoly(flat_polys, h, w)
        self = Masks(encoded, 'compressed_rle')
        return self

    @classmethod
    def demo(Masks):
        from kwimage.structs._mask_backend import cython_mask
        # import kwimage

        # From string
        mask1 = np.array([
            [0, 0, 0, 1, 1, 1, 1, 1, 0],
            [0, 0, 1, 1, 1, 0, 0, 0, 0],
            [0, 0, 1, 1, 1, 1, 1, 1, 0],
            [0, 0, 1, 1, 1, 0, 1, 1, 0],
            [0, 0, 1, 1, 1, 0, 1, 1, 0],
        ], dtype=np.uint8)
        mask2 = np.array([
            [0, 0, 0, 0, 0, 0, 0, 0, 0],
            [0, 0, 1, 1, 1, 0, 0, 0, 0],
            [0, 0, 1, 1, 1, 0, 0, 0, 0],
            [0, 0, 0, 0, 0, 0, 0, 0, 0],
            [0, 0, 0, 0, 0, 0, 0, 0, 0],
        ], dtype=np.uint8)
        mask3 = np.array([
            [0, 0, 0, 1, 1, 0, 0, 1, 0],
            [0, 0, 1, 1, 1, 0, 0, 1, 0],
            [0, 0, 1, 1, 1, 0, 1, 1, 0],
            [0, 0, 1, 1, 1, 1, 1, 1, 0],
            [0, 0, 1, 1, 1, 0, 1, 1, 0],
        ], dtype=np.uint8)
        masks = np.array([mask1, mask2, mask3])

        # The cython utility expects multiple masks in fortran order with the
        # shape [H, W, N], where N is an index over multiple instances.
        fortran_masks = np.asfortranarray(np.transpose(masks, [1, 2, 0]))
        encoded = cython_mask.encode(fortran_masks)
        self = Masks(encoded, format='compressed_rle')
        return self
