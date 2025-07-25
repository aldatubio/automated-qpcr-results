##############################################################################################################################
### About this code
##############################################################################################################################
#
#   This module contains classes to import, analyze, and export qPCR data.
#
#   Different qPCR machines have very different results outputs;
#   because of this, files from different machines need to be handled differently.
#
#   After the user tells the program which machine they used,
#   the program uses that information to choose which algorithm to use to create a usable results dataframe (pandas).
#
#   This dataframe - identical, regardless of the qPCR machine the results file originally came from -
#   is then used to generate a results file.
#
#
#


##############################################################################################################################
### Imports
##############################################################################################################################

import os, sys
sys.path.insert(0, os.path.dirname(__file__)) #look for custom dependencies in shared folder

import pandas as pd #file and data handling
from tkinter import filedialog #prompt user to select results file
import tkinter as tk #error message GUI
import csv #text file parsing
import itertools #for mic csv parsing
import os #for getting file extension
import numpy as np #for least squares regression
import tomli #for assay config
import linreg #to run this script natively, instead of in package context: remove "from . " from this line

pd.set_option('future.no_silent_downcasting', True)



##############################################################################################################################

class DataImporter:
    '''Get qPCR data from text or Excel file, then parse into standardized dataframe.'''
    def __init__(self, cq_cutoff=35,
                 machine_type: str=None, assay: str=None,
                 division='vhf', drm_percentage=0.2,
                 config='assays.toml'):
        ''''''
        # get user-provided parameters
        self.cq_cutoff = cq_cutoff
        self.machine_type = machine_type
        self.assay = assay
        self.division = division
        self.drm_percentage = drm_percentage
        self.configpath = config

        # prepare for assay initialization
        self.reporter_dict = {}
        self.reporter_list = []
        self.ic = None #internal control reporter

        # set other necessary parameters to None / blank, for now
        self.filepath = None
        self.ext = None
        self.head = None
        self.results = None
        self.max_dRn_dict = {}

        # initialize GUI, hide main window
        self.root = tk.Tk()
        self.root.withdraw()

        # get assay config
        with open(os.path.join(os.path.dirname(__file__), self.configpath), mode='rb') as f: #get TOML configuration
            self.config = tomli.load(f)

        
    ##############################################################################################################################
    ### Helper functions for data analysis - file to dataframe
    ##############################################################################################################################

    def isblank(self, row:str):
        '''If string is blank, return True; otherwise, return False.'''
        return all(not field.strip() for field in row)
    

    def csv_to_df(self, csv_file:list, csv_delim:str, results_flag:str):
        '''
        Convert a CSV file into a DataFrame by skipping metadata and extracting relevant results.

        Args:
            csv_file (str): Path to the CSV file.
            csv_delim (str): Delimiter used in the CSV file.
            results_flag (str): Flag to identify the start of the results section.

        Returns:
            pd.DataFrame: Extracted results as a pandas DataFrame.
        '''
        data_bool = False
        data_cleaned = []
        results_reader = csv.reader(csv_file, delimiter = csv_delim)

        for line in results_reader:
            if self.isblank(line): #skip blank lines at the end of the file
                data_bool = False
            if data_bool:
                data_cleaned.append(line)
            if results_flag in line: #begin extracting when the results flag is encountered
                data_bool = True

        header = data_cleaned.pop(0)
        results_table = pd.DataFrame(data_cleaned, columns = header) #first line of cleaned data is the header

        return results_table
    

    def summarize(self, df_dict:dict):
        '''Combine multiple pandas dataframes into a single summary dataframe.'''
        first_loop = True

        for fluor in df_dict:

            columns = ['Well', 'Sample Name', f'{fluor} CT']
            if 'QuantStudio' in self.machine_type:
                columns.append(f'{fluor} Cq Conf')
                columns.append(f'{fluor} dRn')
            if self.division == 'hiv':
                columns.append(f'{fluor} Quantity')
            if not first_loop:
                columns.pop(1)

            if first_loop:
                try:
                    summary_table = df_dict[fluor].loc[:, columns]
                except Exception as e:
                    tk.messagebox.showerror(message='Incorrect file selected. Please try again.\n\n{}'.format(e))
                    # close program
                    raise SystemExit()
                first_loop = False
            else:
                summary_table = pd.merge(summary_table, df_dict[fluor].loc[:, columns], on='Well')
        
        return summary_table
    

    def extract_header(self, reader:csv.reader, flag: str=None, stop: str=None):
        '''
        Extract a header from a CSV reader object.

        Args:
            reader (csv.reader): CSV reader object.
            flag (str): Flag to identify the start of the header section.
            stop (str): Flag to identify the end of the header section.

        Returns:
            list: Extracted header as a list.
        '''
        headbool = False if flag else True # if no flag is defined, start reading header from top of file
        head = []

        for line in reader:
            if not stop:
                # if no stop point is defined, use blank line (isblank) as break point
                if self.isblank(line):
                    headbool = False
            else:
                # if stop is found in current line, stop creating header
                if stop in str(line):
                    headbool = False
            if flag:
                # if flag is defined, look for flag in line; start appending to header if it exists
                if flag in str(line):
                    headbool = True
            if headbool:
                head.append(line)
        return head
    

    def select_file(self, filetypes: list, num_files = 1, extension: bool = True):
        '''Prompt user to select one or more files.
        
        Args:
            filetypes (tuple list): allowable file extensions
            num_files (int): number of files expected
            extension (bool): if True, returns file extension as string

        Returns:
            selected filepath(s): as string or, if multiple, as a list of strings
            (optional) file extension: as string

        '''
        if num_files > 1:
            
            title = 'Choose results files'
            filepaths = filedialog.askopenfilenames(title=title, filetypes=filetypes)
            ext = os.path.splitext(filepaths[0])[1]

            if len(filepaths) != num_files:
                tk.messagebox.showerror(message=f'''Incorrect number of files. Expected {num_files} files,
but {len(filepaths)} were selected. Make sure files are not open in other programs.''')
                # close program
                raise SystemExit()
            
        else: #num_files <= 1
            title = 'Choose results file'
            filepaths = filedialog.askopenfilename(title=title, filetypes=filetypes)
            ext = os.path.splitext(filepaths)[1]
        
        if not filepaths:
            tk.messagebox.showerror(message='No file selected. Make sure file is not open in another program.')
            raise SystemExit()
        
        if extension:
             return filepaths, ext
        else:
            return filepaths


    def extract_results(self, df: pd.DataFrame):
        '''Go through a dataframe row by row, eliminating non-data rows at the top of the dataframe.'''
        # Mic results might not start at expected skiprow - remove any non-numerical data before results table
        row_indices_to_drop = []
        col_names = None

        for row_index in list(df.index.values):
            try:
                int(df.iloc[row_index, 0]) #can we convert the value in the first column ('Well') into an integer? if not, must be text
            except ValueError:
                col_names = list(df.iloc[row_index, :]) #this must then be the header row - copy info to variable
                row_indices_to_drop.append(row_index)

        if col_names is not None:
            for i in row_indices_to_drop:
                df = df.drop(i)    #drop any saved rows
                df.columns = col_names              #replace header
            df.reset_index(inplace=True, drop=True) #fix any index numbering problems that may have occurred as result of row dropping
                
        return df
    

    ##############################################################################################################################
    ### Initialization - get fluors based on selected assay
    ##############################################################################################################################

    def init_reporters(self):
        '''Get reporters and targets, given assay name.'''
        try:
            self.reporter_dict = self.config[self.assay]["assay"]
            self.ic = self.config[self.assay]["ic"]
        
        except Exception as e:
            tk.messagebox.showerror(message='Invalid assay. Check assay input setting.\n\n{}: not defined'.format(e))
            # close program
            raise SystemExit()
        
        self.reporter_list = [key for key in self.reporter_dict]


    ##############################################################################################################################
    ### QuantStudio - file to dataframe
    ##############################################################################################################################

    def parse_qs(self):
        '''Parse QuantStudio 3/5 file into a standardized pandas dataframe.'''
        self.filepath, self.ext = self.select_file(filetypes = [('All Excel Files','*.xlsx'),
                                                                ('All Excel Files','*.xls'),
                                                                ('Text Files', '*.txt')])
        
        
        if self.ext == '.txt': #file extension check - special handling for text files
            with open(self.filepath, newline = '') as csvfile:
                # text file versions of results contain inconsistent formatting throughout file, so reading these straight to a pandas df doesn't work
                # need to work line-by-line instead to get rid of header/footer data
                # get results df:
                results_table = self.csv_to_df(csvfile, '\t', '[Results]')
                # get header as list:
                csvfile.seek(0)
                sheet_reader = csv.reader(csvfile, delimiter=',')
                self.head = self.extract_header(sheet_reader, 'Experiment')
                # text files save values as strings - handle dRn values separated with commas/thousands separator:
                results_table['Delta Rn (last cycle)'] = results_table['Delta Rn (last cycle)'].str.replace(',', '').astype(float)
                if self.division == 'hiv':
                    results_table['Quantity'] = pd.to_numeric(results_table['Quantity'].str.replace(',', ''), errors='coerce')
        
        else: #file is not a text file (so it's an excel file) - excel files cannot be selected if open in another program, so check for this
        
            # get results df:
            file_selected = False
            while not file_selected:
                try:
                    results_table = pd.read_excel(self.filepath, sheet_name = 'Results', skiprows = 43)
                except Exception as e:
                    proceed = tk.messagebox.askretrycancel(message='Incorrect file, or file is open in another program. Click Retry to analyze selected file again.\n\n{}'.format(e),
                                                           icon = tk.messagebox.ERROR)
                    if not proceed:
                        raise SystemExit()
                
                if 'results_table' in locals():
                    file_selected = True

            results_table = self.extract_results(results_table) #fix any skiprows issues

            # get header as list:
            with open(self.filepath, 'rb') as excel_file:
                sheet_csv = pd.read_excel(excel_file, sheet_name = 'Results', usecols='A:B').to_csv(index=False)
                sheet_reader = csv.reader(sheet_csv.splitlines(), delimiter=',')
                self.head = self.extract_header(sheet_reader, 'Experiment')


        try:
            # assign 'Undetermined' wells a CT value - 'CT' column will only exist in correctly formatted results files, so can be used for error checking
            results_table['CT'] = results_table['CT'].replace(to_replace = 'Undetermined', value = self.cq_cutoff)
        except Exception as e:
            tk.messagebox.showerror(message='Unexpected machine type. Check instrument input setting.\n\n{}'.format(e))
            # close program
            raise SystemExit()
        
        # make sure columns contain number values, not strings
        for col in ['CT', 'Cq Conf', 'Baseline End']:
            results_table[col] = results_table[col].apply(pd.to_numeric)
        # clean up HIV-specific columns in similar manner
        if self.division == 'hiv':
            results_table['Quantity'] = results_table['Quantity'].fillna(0)
            results_table['Quantity'] = results_table['Quantity'].apply(pd.to_numeric)


        
        # make sure file and fluor_names have the same fluorophores listed
        if sorted(list(results_table['Reporter'].unique())) != sorted(self.reporter_dict):
            tk.messagebox.showerror(message='''Fluorophores in file do not match expected fluorophores. Check assay assignment.\n
Fluors in selected file: {}
Expected fluors: {}'''.format(sorted(list(results_table["Reporter"].unique())),
                              sorted(self.reporter_dict)))
            # close program
            raise SystemExit()
        
        results_dict = {}
        for fluor in self.reporter_dict:
            
            results_dict[fluor] = results_table.loc[results_table['Reporter'] == fluor]
            try:
                results_dict[fluor] = results_dict[fluor].rename(columns={'Well': 'Well No.',
                                                                          'Well Position': 'Well',
                                                                          'CT': f'{fluor} CT',
                                                                          'Cq Conf': f'{fluor} Cq Conf',
                                                                          'Delta Rn (last cycle)': f'{fluor} dRn'})
                if self.division == 'hiv':
                    results_dict[fluor] = results_dict[fluor].rename(columns={'Quantity': f'{fluor} Quantity'})
        
            except Exception as e:
                tk.messagebox.showerror(message='Fluorophores in file do not match those entered by user. Check fluorophore assignment.\n\n{}'.format(e))
                # close program
                raise SystemExit()
            
            # get max dRn, for use later
            if self.reporter_dict[fluor] == 'Internal Control':
                baseline_end = 5
            else:
                baseline_end = 10
            get_max = results_dict[fluor].loc[results_dict[fluor]['Baseline End'] >= baseline_end, [f'{fluor} dRn']]
            self.max_dRn_dict[fluor] = float(get_max[f'{fluor} dRn'].max())
        
        self.results = self.summarize(results_dict)


    ##############################################################################################################################
    ### Rotor-Gene - file to dataframe
    ##############################################################################################################################

    def parse_rgq(self):
        '''Parse Rotor-Gene Q results files into a standardized pandas dataframe.'''
        results_filepaths = self.select_file(filetypes = [('Text Files', '*.csv')],
                                         num_files=len(self.reporter_dict),
                                         extension=False)
    
        self.filepath = os.path.commonprefix(results_filepaths)
        first_loop = True
        used_filepaths = []


        for filepath in results_filepaths:

            results_table = pd.read_csv(filepath, skiprows = 27)

            # see if files chosen are correct - if the file is a valid results file, it will have a column called 'Ct'
            try:
                results_table['Ct'] = results_table['Ct'].fillna(self.cq_cutoff)
            except Exception as e:
                tk.messagebox.showerror(message='Incorrect files selected. Please try again.\n\n{}'.format(e))
                # close program
                raise SystemExit()

            # cycle through all fluors needed for selected assay, match them to names of files selected
            for fluor in self.reporter_dict:
                if f'{fluor}.csv' in filepath or f'{self.reporter_dict[fluor]}.csv' in filepath:
                    used_filepaths.append(filepath)
                    results_table = results_table.rename(columns={'No.': 'Well',
                                                                'Name': 'Sample Name',
                                                                'Ct': f'{fluor} CT',
                                                                'Ct Comment': 'Comments',
                                                                'Given Conc (copies/reaction)': 'Copies'})
                    # first time loop is run:
                    if first_loop:
                        # initialize summary table
                        self.results = results_table.loc[:, ['Well', 'Sample Name', 'Copies', 'Comments', f'{fluor} CT']]
                        # and get header info
                        with open(filepath, 'r') as csv_file:        
                            sheet_reader = csv.reader(csv_file, delimiter=',')
                            self.head = self.extract_header(sheet_reader, stop='Quantitative')
                        first_loop = False
                    else:
                        self.results = pd.merge(self.results, results_table.loc[:, ['Well', f'{fluor} CT']], on='Well')

        # number of files was determined to be correct, but did every file get used? if not, results are incomplete
        if sorted(results_filepaths) != sorted(used_filepaths):
            tk.messagebox.showerror(message='Incorrect files selected, or file names have been edited. Please try again.')
            # close program
            raise SystemExit()


    ##############################################################################################################################
    ### Mic - file to dataframe
    ##############################################################################################################################

    def parse_mic(self):
        '''Parse Mic results file into a standardized pandas dataframe.'''
        self.filepath, self.ext = self.select_file(filetypes = [('All Excel Files','*.xlsx'),
                                                                ('All Excel Files','*.xls'),
                                                                ('Text Files', '*.csv')])
        tabs_to_use = {}

        if self.ext == '.csv': #file extension check - special handling for text files
            
            # text file versions of results contain inconsistent formatting throughout file, so reading these straight to a pandas df doesn't work
            # need to work line-by-line instead to get rid of header/footer data
            with open(self.filepath, newline = '') as csvfile:
                
                # make list of lines
                csv_lines = csvfile.readlines()

                # get header info while we're here
                csvfile.seek(0)
                sheet_reader = csv.reader(csvfile, delimiter=',')
                self.head = self.extract_header(sheet_reader, stop='Log')

            # create 'chunks' list: break csv into groups, separated by blank lines
            chunks = [list(group) for is_blank, group in itertools.groupby(csv_lines, lambda line: line.strip() == '') if not is_blank]
            results_csvs = {}

            for fluor in self.reporter_dict:
                for chunk in chunks:
                    title = chunk[0] #first line of chunk
                    if 'Start Worksheet - Analysis - Cycling' in title and 'Result' in title and (fluor in title or self.reporter_dict[fluor] in title):
                        results_csvs[fluor] = chunk
                        tabs_to_use[fluor] = title
                        break
                    elif self.division == 'hiv' and 'Start Worksheet - Samples' in title: #get sample info (control vs unknown, concentration if available) while we're here
                        info_csv = chunk

        else: #file is not a text file - must be Excel
            sheetnames = pd.ExcelFile(self.filepath).sheet_names #get tabs in file
            for fluor in self.reporter_dict:
                for tab in sheetnames: #cycle through fluorophores and tabs, assign tabs to fluorophores
                    if self.reporter_dict[fluor] in tab and 'Result' in tab and 'Absolute' not in tab:
                        tabs_to_use[fluor] = tab
                        break

            # get header info
            with open(self.filepath, 'rb') as excel_file:

                try:
                    sheet_csv = pd.read_excel(excel_file, sheet_name = 'General Information', usecols='A:B').to_csv(index=False)
                except Exception as e:
                    tk.messagebox.showerror(message='Incorrect file selected. Check instrument input.\n\n{}'.format(e))
                    # close program
                    raise SystemExit()
                
                sheet_reader = csv.reader(sheet_csv.splitlines(), delimiter=',')
                self.head = self.extract_header(sheet_reader, stop='Log')
            
                # get sample task assignment (controls vs unknowns) and assigned copy numbers, if any
                if self.division == 'hiv':
                    info_df = pd.read_excel(excel_file, sheet_name = "Samples"
                                        ).loc[:, ['Well', 'Type', 'Standards Concentration (Copies/µL)']
                                        ].rename(columns={'Type': 'Task', 'Standards Concentration (Copies/µL)': 'Assigned Quantity'})


        if sorted(tabs_to_use) != sorted(self.reporter_dict): #check to make sure fluorophores with assigned tabs match the list of fluors known to be in assay
            tk.messagebox.showerror(message='''Fluorophores in file do not match expected fluorophores. Check fluorophore and assay assignment.\n\n
Expected: {e}\nGot: {g}'''.format(e=sorted(self.reporter_dict), g=sorted(tabs_to_use)))
            # close program
            raise SystemExit()
        
        results_dict = {}
        first_loop = True
        for fluor in self.reporter_dict:
            
            if self.ext == '.csv':
                results_dict[fluor] = self.csv_to_df(results_csvs[fluor], ',', 'Results')
                if self.division == 'hiv' and first_loop:
                    info_df = self.csv_to_df(info_csv, ','
                                            ).loc[:, ['Well', 'Type', 'Standards Concentration (Copies/ÂµL)']
                                            ].rename(columns={'Type': 'Task', 'Standards Concentration (Copies/ÂµL)': 'Assigned Quantity'})
                    info_df['Assigned Quantity'] = info_df['Assigned Quantity'].apply(pd.to_numeric)
                    info_df['Well'] = info_df['Well'].astype(int)
                    first_loop = False
            
            else:
                results_dict[fluor] = pd.read_excel(self.filepath, sheet_name = tabs_to_use[fluor], skiprows = 32)
                # Mic results might not start at expected skiprow - remove any non-numerical data before results table
                results_dict[fluor] = self.extract_results(results_dict[fluor])

            results_dict[fluor] = results_dict[fluor].rename(columns={'Cq': f'{fluor} CT'})
            results_dict[fluor]['Well'] = results_dict[fluor]['Well'].astype(int)
            results_dict[fluor][f'{fluor} CT'] = results_dict[fluor][f'{fluor} CT'].fillna(self.cq_cutoff).apply(pd.to_numeric)
            
            if self.division == 'hiv':
                info_df['Well'] = info_df['Well'].astype(int)
                results_dict[fluor] = pd.merge(results_dict[fluor], info_df, on='Well')

                m, b = linreg.linreg(df=results_dict[fluor], fluor=fluor, percent_drm=self.drm_percentage)
                results_dict[fluor][f'{fluor} Quantity'] = results_dict[fluor][f'{fluor} CT'].apply(linreg.quantify, args=(m,b))
        
        self.results = self.summarize(results_dict)


    ##############################################################################################################################
    ### Parse function - serves as 'main' function
    ##############################################################################################################################

    def parse(self):
        '''Initialize reporters, then run parsing function.'''
        self.init_reporters()
        
        if self.machine_type == 'Rotor-Gene':
            self.parse_rgq()
        elif self.machine_type == 'Mic':
            self.parse_mic()
        elif 'QuantStudio' in self.machine_type:
            self.parse_qs()
        else:
            raise ValueError


class DataAnalyzer:
    '''Given a parsed qPCR dataframe, generate qualitative results.'''
    def __init__(self, data:DataImporter,
                       pos_cutoff=30,
                       dRn_percent_cutoff=0.05,
                       min_drm_percent=0.05,
                       max_drm_percent=0.1):
        
        self.df = data.results
        self.machine_type = data.machine_type
        self.reporter_dict = data.reporter_dict
        self.reporter_list = data.reporter_list
        self.ic = data.ic
        self.max_dRn = data.max_dRn_dict
        self.cq_cutoff = data.cq_cutoff

        #vhf only
        self.pos_cutoff = pos_cutoff
        self.dRn_percent_cutoff = dRn_percent_cutoff

        #hiv only
        self.min_drm_percent = min_drm_percent
        self.max_drm_percent = max_drm_percent


    def vhf_result(self, row):
        '''Determine the qualitative result for a given sample, based on qPCR data.
        
           This function is meant for the handling of viral hemorrhagic fever data, and does not accomodate HIV quantitative results.
        '''
        # first value - index 0 - in list corresponds to internal control
        # therefore, fill this list index with high number
        cq_vals = [98]

        # iterate through all other (non-IC) fluorophores for the row
        for i in range(1, len(self.reporter_list)):
            # if a positive signal is observed (Cq below cutoff plus dRn is >5% of max on plate), add it to the list
            if self.machine_type == 'QuantStudio 3' or self.machine_type == 'QuantStudio 5':
                if (row[self.reporter_list[i] + ' CT'] < self.pos_cutoff and
                    row[self.reporter_list[i] + ' dRn']/self.max_dRn[self.reporter_list[i]] > self.dRn_percent_cutoff
                    ):
                    cq_vals.append(row[self.reporter_list[i] + ' CT'])
                # otherwise, add another high number
                else:
                    cq_vals.append(99)
            else:
                if row[self.reporter_list[i] + ' CT'] < self.pos_cutoff: #RotorGene and Mic have internal dRn cutoff handling
                    cq_vals.append(row[self.reporter_list[i] + ' CT'])
                else:
                    cq_vals.append(99)

        # find the minimum of the list of Cq values
        fluor_min = cq_vals.index(min(cq_vals))
        # if the minimum is not the artificial internal control / index-0 value, well was positive for something
        # well tested positive for whatever the lowest observed Cq value was (if more than one fluorophore present)
        if fluor_min != 0:
            return f'{self.reporter_dict[self.reporter_list[fluor_min]]} Positive'
        # otherwise, check IC amplification - was it successful? if so, this is a negative reaction
        elif row[self.ic + ' CT'] < self.pos_cutoff:
            return 'Negative'
        # nothing amplified, including IC? result invalid
        else:
            return 'Invalid Result'
        

    def vhf_analysis(self):
        '''Perform analysis on all rows in dataframe.
        
           References `vhf_result`.
        '''
        self.df['Result'] = self.df.apply(self.vhf_result, axis=1)



    def hiv_result(self, row, col):
        if row[col] < self.min_drm_percent or row[self.ic + ' Quantity'] < 50:
            call = 'Negative'
        elif row[col] >= self.max_drm_percent:
            call = 'Positive'
        else:
            call = 'Indeterminate'
        return call


    def hiv_analysis(self):
        '''Perform analysis on all rows in dataframe.
        
           References `hiv_result`.
        '''
        #iterate through all reporters except index 0, which holds the internal control reporter
        for fluor in self.reporter_list[1:]:
            
            # create additional columns containing DRM percentages
            kwargs = {f'{self.reporter_dict[fluor]} DRM Percentage': 
                      lambda x: x[fluor + ' Quantity'] / x[self.ic + ' Quantity']}
            self.df = self.df.assign(**kwargs).fillna(0)
            
            # based on DRM percentage columns, determine call for each reporter
            self.df[f'{self.reporter_dict[fluor]} Call'] = self.df.apply(self.hiv_result, col=f'{self.reporter_dict[fluor]} DRM Percentage', axis=1)

    

class DataExporter:
    '''Given an analyzed qPCR dataframe, clean up and export results.'''
    def __init__(self, imported:DataImporter,
                       analyzed:DataAnalyzer,
                       columns:list):
        
        self.header = imported.head
        self.results = analyzed.df
        self.division = imported.division
        self.machine_type = analyzed.machine_type
        self.reporter_list = analyzed.reporter_list
        self.reporter_dict = analyzed.reporter_dict
        self.ic = imported.ic
        self.src_filepath = imported.filepath
        self.dest_filepath = None
        self.columns = columns
    

    def prepend(self):
        '''Add a line or header to the beginning of a file.'''
        with open(self.dest_filepath, 'r+', newline='', errors='replace') as file:
            existing = file.read() #save file contents to 'existing'
            file.seek(0) #move pointer back to start of file
            if isinstance(self.header, str): #if header is single line
                file.write(self.header+'\n\n'+existing)
            else:
                # header is a list - need to make writer csv object, then write list to file item by item
                writer = csv.writer(file)
                writer.writerows(self.header)
                file.write('\n\n'+existing)


    def roundvals(self):
        '''Round values for presentation purposes.'''
        for key in self.reporter_dict:
            self.results[f'{key} CT'] = self.results[f'{key} CT'].round(1)
            if 'QuantStudio' in self.machine_type:
                self.results[f'{key} Cq Conf'] = self.results[f'{key} Cq Conf'].round(3)
                self.results[f'{key} dRn'] = self.results[f'{key} dRn'].round(1)
            if self.division == 'hiv':
                self.results[f'{key} Quantity'] = self.results[f'{key} Quantity'].round(0).astype(int)
                if key != self.ic:
                    self.results[f'{self.reporter_dict[key]} DRM Percentage'] = self.results[f'{self.reporter_dict[key]} DRM Percentage'].map(
                        lambda num: '{0:.1f}%'.format(round(num*100, 1) if num < 1 else 100))


    def get_column_list(self):
        '''Create list of columns to export.'''
        for i in range(len(self.reporter_list)):
            
            self.results = self.results.rename(columns={f'{self.reporter_list[i]} CT': f'{self.reporter_dict[self.reporter_list[i]]} Cq'})
            
            if 'QuantStudio' in self.machine_type:
                self.results = self.results.rename(columns={f'{self.reporter_list[i]} Cq Conf': f'{self.reporter_dict[self.reporter_list[i]]} Cq Conf',
                                                              f'{self.reporter_list[i]} dRn': f'{self.reporter_dict[self.reporter_list[i]]} dRn'})
            if self.division == 'hiv':
                self.results = self.results.rename(columns={f'{self.reporter_list[i]} Quantity': f'{self.reporter_dict[self.reporter_list[i]]} Copies'})


    def cleanup(self):
        '''Get rid of unwanted columns in analyzed qPCR dataframe.'''
        rm_headers = list(self.results) #get list of headers to remove - begins with all headers in list

        for header in self.columns:
            if 'Cq' in header and 'Cq Conf' not in header:
                for key in self.reporter_dict:
                    rm_headers.remove(f'{self.reporter_dict[key]} Cq')
            elif 'dRn' in header:
                for key in self.reporter_dict:
                    rm_headers.remove(f'{self.reporter_dict[key]} dRn')
            elif 'Call' in header:
                for key in self.reporter_list[1:]:
                    rm_headers.remove(f'{self.reporter_dict[key]} Call')
            elif 'DRM Percentage' in header:
                for key in self.reporter_list[1:]:
                    rm_headers.remove(f'{self.reporter_dict[key]} DRM Percentage')
            else:
                try:
                    rm_headers.remove(header) #for every header in list of columns to export, remove this from our list
                                          #(leaving behind only the columns to get rid of)
                except:
                    pass

        self.results = self.results.drop(columns=rm_headers, axis=1)


    def to_csv(self):
        '''Export analyzed qPCR dataframe to CSV.'''

        self.dest_filepath = os.path.splitext(self.src_filepath)[0]+' - Summary.csv'

        # results file can't be created/written if the user already has it open - catch possible PermissionErrors
        file_saved = False
        while not file_saved:
            try:
                self.results.to_csv(
                    path_or_buf=self.dest_filepath,
                    index=False
                    )
                self.prepend()
                file_saved = True

            # if file couldn't be saved, let the user know
            except Exception as e:
                proceed = tk.messagebox.askretrycancel(message='Unable to write results file. Make sure results file is closed, then click Retry to try again.\n\n{}'.format(e),
                                                       icon = tk.messagebox.ERROR)
                if not proceed:
                    raise SystemExit()
                
        tk.messagebox.showinfo(title='Success', message=f'CSV summary saved:\n\n{self.dest_filepath}')

    
    def export(self):
        self.roundvals()
        self.get_column_list()
        self.cleanup()
        self.to_csv()


if __name__ == '__main__':
    importer = DataImporter(assay="082AFT 084V", machine_type="Mic", division='hiv')
    importer.parse()
    print('Data imported')
    print(importer.results)
    analyzer = DataAnalyzer(data=importer)
    analyzer.hiv_analysis()
    print('Data analyzed')
    print(analyzer.df)
    exporter = DataExporter(importer, analyzer, columns=["Well", "Sample Name", "Call", "DRM Percentage", "VQ Quantity"])
    exporter.export()
    print('Data exported')
    print(exporter.results)
