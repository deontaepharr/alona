import re
import os
import uuid
import inspect
import logging
import subprocess
import magic

from .log import (log_info, log_error)
from .exceptions import *

class AlonaBase():
    """
    AlonaBase class
    """

    params = None

    def __init__(self, params=None):
        if params is None:
            params = {}

        self.params = {
        }

        self._is_binary = None
        self._delimiter = None
        self._has_header = None

        self.params.update(params)

        # Set default options
        if self.params['output_directory'] is None:
            self.params['output_directory'] = 'alona_out_%s' % self.random()
        if self.params['loglevel'] == 'debug':
            logging.debug('*** parameters *********************')
            for par in self.params:
                logging.debug('%s : %s', par, self.params[par])
            logging.debug('************************************')

    def get_matrix_file(self):
        return '%s/input.mat' % self.params['output_directory']

    def random(self):
        """ Get random 8 character string """
        return str(uuid.uuid4()).split('-')[0]

    def create_work_dir(self):
        """ Creates a working directory for temporary and output files. """
        try:
            logging.debug('creating output directory: %s', self.params['output_directory'])
            os.mkdir(self.params['output_directory'])
        except FileExistsError:
            log_error(self, 'Error: Output directory already exists (%s)' %
                      self.params['output_directory'])
            raise

    def is_file_empty(self):
        if os.stat(self.params['input_filename']).st_size == 0:
            raise FileEmptyError()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self

    def is_binary(self, filename):
        with open(filename, 'rb') as fh:
            for block in fh:
                if b'\0' in block:
                    return True
        return False

    def unpack_data(self):
        """ Unpacks compressed data and if no compression is used, symlinks to data. """
        # Is the file binary?
        self._is_binary = self.is_binary(self.params['input_filename'])
        abs_path = os.path.abspath(self.params['input_filename'])
        mat_out = self.get_matrix_file()

        if self._is_binary:
            logging.debug('Input file is binary.')

            out = subprocess.check_output("file %s" % (self.params['input_filename']),
                                          shell=True)

            out = out.decode('ascii')

            if re.search(' gzip compressed data,', out):
                logging.debug('gzip data detected.')

                # confirm integrity
                try:
                    out = subprocess.check_output('gzip -t %s' % (abs_path), shell=True,
                                                  stderr=subprocess.STDOUT)

                    out = out.decode('ascii')
                except subprocess.CalledProcessError as exc:
                    if re.search('unexpected end of file', exc.output.decode('ascii')):
                        raise FileCorruptError('Error: input file is corrupt.')

                # uncompress
                os.system('gzip -d -c %s > %s' % (abs_path, mat_out))
            elif re.search(' Zip archive data,', out):
                logging.debug('zip data detected.')

                # confirm integrity
                try:
                    # don't use Zip -T because it only accepts files ending with .zip
                    out = subprocess.check_output("gzip -t %s" % (abs_path), shell=True,
                                                  stderr=subprocess.STDOUT)

                    out = out.decode('ascii')
                except subprocess.CalledProcessError as exc:
                    if re.search(' unexpected end of file', exc.output.decode('ascii')):
                        raise FileCorruptError('Error: input file is corrupt.')

                # check that the archive only contains one file
                out = subprocess.check_output('unzip -v %s | wc -l' % (abs_path),
                                              shell=True)

                out = out.decode('ascii').replace('\n', '')
                no_files = int(out) - 5

                if no_files != 1:
                    raise Exception('More than one file in input archive.')

                # Files  created  by  zip  can be uncompressed by gzip only if they have
                # a single member compressed with the 'deflation' method.
                os.system('zcat %s > %s' % (abs_path, mat_out))
            elif re.search(' bzip2 compressed data,', out):
                logging.debug('bzip2 data detected.')

                # confirm integrity
                try:
                    out = subprocess.check_output('bzip2 -t %s' % (abs_path), shell=True,
                                                  stderr=subprocess.STDOUT)

                    out = out.decode('ascii')
                except subprocess.CalledProcessError as exc:
                    if re.search('file ends unexpectedly', exc.output.decode('ascii')):
                        raise FileCorruptError('Input file is corrupt.')

                # uncompress
                os.system('bzip2 -d -c %s > %s' % (abs_path, mat_out))
            else:
                raise InvalidFileFormatError('Invalid format of the input file.')
        else:
            logging.debug('Input file is not binary.')
            # Create a symlink to the data
            cmd = 'ln -sfn %s %s' % (abs_path, mat_out)
            os.system(cmd)

        mag = magic.Magic(mime=True)
        # don't use `from_file` (it doesn't follow symlinks)
        f_type = mag.from_buffer(open(mat_out, 'r').read(1024))

        if f_type != 'text/plain':
            raise InputNotPlainTextError('Input file is not plain text (found type=%s).' %
                                         f_type)

    def cleanup(self):
        """ Removes temporary files. """
        # remove temporary files
        for garbage in ('input.mat',):
            logging.debug('removing %s', garbage)

            try:
                os.remove('%s/%s' % (self.params['output_directory'], garbage))
            except FileNotFoundError:
                logging.debug('Not found: %s', garbage)

    def __guess_delimiter(self):
        dcount = {' ' : 0, '\t' : 0, ',' : 0}
        i = 0
        fh = open(self.get_matrix_file(), 'r')

        for line in fh:
            dcount[' '] += line.count(' ')
            dcount['\t'] += line.count('\t')
            dcount[','] += line.count(',')

            i += 1

            if i > 10:
                break

        fh.close()
        d_sorted = sorted(dcount, key=dcount.get, reverse=True)
        return d_sorted[0]

    def get_delimiter(self):
        """ Figures out the data delimiter of the input data. """
        used_delim = ''
        if self.params['delimiter'] == 'auto':
            used_delim = self.__guess_delimiter()
        else:
            used_delim = self.params['delimiter'].upper()
            if used_delim == 'TAB':
                used_delim = '\t'
            elif used_delim == 'SPACE':
                used_delim = ' '

        logging.debug('delimiter is: "%s" (ASCII code=%s)', used_delim, ord(used_delim))
        self._delimiter = used_delim
        return used_delim

    def has_header(self):
        """ Figures out if the input data uses a header or not. """
        ret = None
        if self.params['header'] == 'auto':
            fh = open(self.get_matrix_file(), 'r')
            first_line = next(fh).replace('\n', '')

            total = 0
            count_digit = 0

            for item in first_line.split(self._delimiter):
                if item.replace('.', '', 1).isdigit():
                    count_digit += 1
                total += 1

            fh.close()

            ret = not total == count_digit
        else:
            ret = (self.params['header'] == 'yes')

        # if all fields are non-numerical, it's likely a header
        self._has_header = ret

        logging.debug('has header: %s', self._has_header)

        return ret

    def sanity_check_columns(self):
        """ Sanity check on data integrity. Raises an exception if column count is not
            consistent. """
        fh = open(self.get_matrix_file(), 'r')

        if self._has_header:
            next(fh)

        cols = {}

        for line in fh:
            no_columns = len(line.split(self._delimiter))
            cols[no_columns] = 1

        fh.close()

        if len(cols.keys()) > 1:
            raise IrregularColumnCountError('Rows in your data matrix have different number \
of columns (every row must have the same number of columns).')
        log_info('%s cells detected.' % '{:,}'.format(cols.popitem()[0]))

    def sanity_check_genes(self):
        """ Sanity check on gene count. Raises an exception if gene count is too low. """
        fh = open(self.get_matrix_file(), 'r')
        if self._has_header:
            next(fh)

        count = 0
        for line in fh:
            count += 1
            
        fh.close()

        if count < 1000:
            raise IrregularGeneCountError('Number of genes in the input data is too low.')
        log_info('%s genes detected' % '{:,}'.format(count))

    def ortholog_mapper(self):
        """ Maps mouse genes to the corresponding human ortholog.
            Only one-to-one orthologs are considered. """
        # human gene symbols to ens
        f = open(os.path.dirname(inspect.getfile(AlonaBase)) +
                 '/genome/hgnc_complete_set.txt', 'r')
        hs_symb_to_hs_ens = {}

        for line in f:
            if re.search(r'^\S+\t\S+\t', line) and re.search('(ENSG[0-9]+)', line):
                hs_symbol = line.split('\t')[1]
                hs_ens = re.search('(ENSG[0-9]+)', line).group(1)
                hs_symb_to_hs_ens[hs_symbol] = hs_ens
        f.close()

        # ortholog mappings
        f = open(os.path.dirname(inspect.getfile(AlonaBase)) +
                 '/genome/human_to_mouse_1_to_1_orthologs.tsv', 'r')
        next(f)

        human_to_mouse = {}
        for line in f:
            if re.search('\tortholog_one2one\t', line):
                foo = line.split('\t')
                human_ens = foo[0]
                mouse_ens = foo[1]
                human_to_mouse[human_ens] = mouse_ens
        f.close()

        f = open(self.get_matrix_file(), 'r')
        ftemp = open(self.get_matrix_file() + '.mapped2mouse.mat', 'w')
        ftemp2 = open(self.get_matrix_file() + '.genes_missing_mouse_orthologs', 'w')

        if self._has_header:
            header = next(f)
            ftemp.write(header)

        orthologs_found = 0

        for line in f:
            # remove quotes
            line = re.sub('"', '', line)
            foo = line.split(self._delimiter)

            gene = foo[0]

            if re.search('.+_ENSG[0-9]+', gene):
                gene = re.search('^.+_(ENSG[0-9]+)', gene).group(1)
            if human_to_mouse.get(gene, '') != '':
                new_gene_name = human_to_mouse[gene]
                ftemp.write('%s%s%s' % (new_gene_name,
                                        self._delimiter,
                                        self._delimiter.join(foo[1:])))
                orthologs_found += 1
            elif hs_symb_to_hs_ens.get(gene, '') != '':
                hs_ens = hs_symb_to_hs_ens[gene]
                mm_ens = human_to_mouse.get(hs_ens, '')
                orthologs_found += 1
                if mm_ens != '':
                    ftemp.write('%s%s%s' % (mm_ens,
                                            self._delimiter,
                                            self._delimiter.join(foo[1:])))
                else:
                    ftemp2.write('%s\n' % (gene))

        f.close()
        ftemp.close()
        ftemp2.close()

        log_info('mapped %s genes to mouse orthologs' % ('{:,}'.format(orthologs_found)))

        return self.get_matrix_file() + '.mapped2mouse.mat'

    def sanity_check_gene_dups(self):
        """ Checks for gene duplicates. """
        with open(self.get_matrix_file(), 'r') as f:
            if self._has_header:
                next(f)

            genes = {}

            for line in f:
                gene = line.split(self._delimiter)[0]
                if not gene in genes:
                    genes[gene] = 1
                else:
                    genes[gene] += 1

            if gene in genes:
                if genes[gene] > 1:
                    raise GeneDuplicatesError('Gene duplicates detected.')

    def map_genes_to_ref(self):
        """ Maps gene symbols to internal gene symbols. """
        data = []
        logging.debug('Mapping genes to reference.')

        ftemp = open(self.get_matrix_file() + '.C', 'w')
        with open(self.get_matrix_file(), 'r') as f:
            if self._has_header:
                header = next(f)
                ftemp.write(header)

            for line in f:
                data.append(line.replace('"', ''))

        genes_found = {}
        switch = 0
        total = 0
        unmappable = []

        # are gene symbols "Entrez"? these gene symbols consists of numbers only.
        is_entrez_gene_id = 1

        for line in data:
            foo = line.split(self._delimiter)
            gene = foo[0]
            if not re.search('^[0-9]+$', gene):
                is_entrez_gene_id = 0

        if is_entrez_gene_id: logging.debug('Gene symbols appear to be Entrez.')
